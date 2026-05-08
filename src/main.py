import os
from openai import OpenAI
from docx import Document
from pathlib import Path
import json
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import pdfplumber
import requests
import re

# 1. Leer las variables del entorno Docker
uri_mongo = os.getenv("MONGO_URI")
ollama_host = os.getenv("OLLAMA_HOST", "http://ollama:11434")
api_key_openrouter = os.getenv("OPENROUTER_API_KEY")


# 2. Conexión a tu IA Local (Ollama) usando el cliente de OpenAI
# modelo_llm = os.getenv("OLLAMA_MODEL", "llama3.2") # Coge el modelo de tu .env, si no hay pone "llama3.2"
# cliente = OpenAI(
#     base_url=f"{ollama_host}/v1", # Le añadimos /v1 porque así lo pide la librería
#     api_key="ollama", # Ollama es gratis y local, no necesita key, pero la librería exige que pongamos algo
# )

# conexion a openrouter
# modelo_llm = "liquid/lfm-2.5-1.2b-thinking:free" # solo saca una pregunta
modelo_llm = "google/gemma-4-31b-it:free"
cliente = OpenAI(
    base_url="https://openrouter.ai/api/v1", # <--- APUNTAMOS A INTERNET
    api_key=api_key_openrouter,              # <--- USAMOS TU CLAVE REAL
)

# url kahoot json
url_kahoot = "https://create.kahoot.it/rest/kahoots/"

texto_del_documento = ""
texto_metadatos = ""

def extraer_texto_docx(ruta_docx):
    doc = Document(ruta_docx)
    output = []

    # Iteramos por todos los elementos del cuerpo del documento
    for element in doc.element.body:
        # Detectar Párrafos
        if element.tag.endswith('p'):
            # Encontrar el objeto párrafo correspondiente
            paragraph = [p for p in doc.paragraphs if p._element == element]
            if paragraph and paragraph[0].text.strip():
                texto = paragraph[0].text.strip()
                output.append(texto)

        # Detectar Tablas
        elif element.tag.endswith('tbl'):
            tabla_obj = [t for t in doc.tables if t._element == element][0]
            output.append("\n[TABLA_START]")
            
            for i, row in enumerate(tabla_obj.rows):
                # Extraer texto de cada celda limpiando saltos de linea
                celdas = [cell.text.replace('\n', ' ').strip() for cell in row.cells]
                row_str = "| " + " | ".join(celdas) + " |"
                output.append(row_str)
                
                # Si es la primera fila (header), añadir la linea separadora de Markdown
                if i == 0:
                    separador = "| " + " | ".join(["---"] * len(celdas)) + " |"
                    output.append(separador)
            
            output.append("[TABLA_END]\n")

    # print(output)
    return "\n".join(output)


def extraer_texto_pdf(ruta_pdf):
    output = []
    with pdfplumber.open(ruta_pdf) as pdf:
        for pagina in pdf.pages:

            # extaer 
            texto_pagina = pagina.extract_text(x_tolerance=3, x_tolerance_ratio=None, y_tolerance=3, layout=False, x_density=7.25, y_density=13, line_dir_render=None, char_dir_render=None)
            if texto_pagina:
                output.append(texto_pagina)

            # obtener las tablas de la pagina
            tablas = pagina.extract_tables()

            # formatear las tablas para que las entienda el llm
            for tabla in tablas:
                output.append("\n[TABLA_START]")
                
                for fila in tabla:
                    # A veces hay celdas vacías (None). Las cambiamos por texto vacío ""
                    # y quitamos los saltos de línea internos de las celdas para no romper el formato
                    fila_limpia = [str(celda).replace('\n', ' ') if celda else "" for celda in fila]
                    
                    # Unimos la fila con el separador "|"
                    fila_str = "| " + " | ".join(fila_limpia) + " |"
                    output.append(fila_str)
                    
                output.append("[TABLA_END]\n")

    # print(output)
    return "\n".join(output)


def procesar_respuesta_llm(respuesta_llm):
    # eliminamos bloques de codigo markdown
    texto_limpio = respuesta_llm.strip()
    if texto_limpio.startswith("```"):
        # primera linea (```json) y la ultima (```) las quitamos
        lineas = texto_limpio.splitlines()
        texto_limpio = "\n".join(lineas[1:-1]) if lineas[0].startswith("```") else texto_limpio

    try:
        # devolvemos una lista python
        return json.loads(texto_limpio)

    except json.JSONDecodeError as e:
        print(f"Error al decodificar: {e}")
        return None
    

def comprobar_respuesta_llm(respuesta_llm, campos_requeridos):
    # comprobamos si existe un formato json
    match = re.search(r"\[.*\]", respuesta_llm, re.DOTALL)

    # si not match no hay un JSON valido
    if not match:
        print("No es un JSON válido")
        return None

    # obtenemos el json, desde '[' hasta ']'
    datos_json = match.group(0)

    # intentamos convertirlo a un diccionario, si hay algun typo dara error
    try:
        datos_json =  json.loads(datos_json)
    except json.JSONDecodeError:
        print("Había algun error en el JSON")
        return None
    
    # comprobamos si esta vacio
    if len(datos_json) == 0:
        print("❌ El JSON está vacío, no ha extraído ninguna pregunta.")
        return None

    for pregunta in datos_json:
        # Convertimos las claves del diccionario de la IA en un Set
        claves_ia = set(pregunta.keys())
        
        # Comparamos si son EXACTAMENTE iguales
        if claves_ia != campos_requeridos:
            print(f"Error de formato: La IA ha devuelto campos incorrectos.")
            # Aquí podrías ver qué falta o sobra para decírselo al LLM en el reintento:
            # faltan = campos_requeridos - claves_ia
            # sobran = claves_ia - campos_requeridos
            return None
    
    return datos_json
    

# funcion principal para hacer las llamadas a la api y guardar en la base de datos
# llamada a la api del llm
def llamada_llm(info = False):
    
    i = 5
    datos = None

    while (i > 0):

        if not info:
            completion = cliente.chat.completions.create(
            extra_headers={},
            extra_body={},
            model=modelo_llm,
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Eres un extractor de datos técnicos preciso. Tu única función es transformar un JSON de Kahoot a una LISTA de objetos JSON.\n"
                        "REGLA DE ORO: Debes generar UN objeto JSON por cada pregunta encontrada en el array 'questions'. No omitas ninguna.\n"
                        "REGLA DE COPIA: El 'Enunciado_completo' debe incluir la pregunta seguida de todas sus opciones (A, B, C, D).\n"
                        "REGLA DE CURSO: Elige estrictamente entre 'Primero', 'Segundo', 'Tercero', 'Cuarto'.\n"
                        "REGLA DE SOLUCIÓN: Escribe el texto exacto de la opción marcada como 'correct': true."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        "Transforma TODAS las preguntas de este examen a este formato de lista de Python [{}, {}, ...]:\n"
                        "[\n"
                        "  {\n"
                        '    "Asignatura": "...",\n'
                        '    "Curso": "Primero, Segundo, Tercero o Cuarto",\n'
                        '    "Estudios": "...",\n'
                        '    "Enunciado_completo": "...Si es tipo test, pon las posibles respuestas en el enunciado",\n'
                        '    "Solución": "..."\n'
                        "  }\n"
                        "]\n\n"
                        f"TEXTO DEL EXAMEN:\n{texto_del_documento}"
                    )
                }
            ],
            temperature = 0
            )

            campos_requeridos = {"Asignatura", "Curso", "Estudios", "Enunciado_completo", "Solución"}


        else: # si ya tenemos metadatos rellanados hacemos este otro prompt
            completion = cliente.chat.completions.create(
            extra_headers={},
            extra_body={},
            model=modelo_llm,
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Eres un extractor de datos académicos estructurados. Tu salida debe ser EXCLUSIVAMENTE "
                        "una LISTA de objetos JSON (un array de Python [{}, {}]).\n"
                        "REGLA CRÍTICA DE FUSIÓN: Vas a recibir dos textos: 'METADATOS' y 'EXAMEN'. "
                        "Debes crear un objeto JSON por CADA pregunta que encuentres en el texto del EXAMEN.\n"
                        "REGLA CRÍTICA DE COPIA: Para cada pregunta, debes COPIAR EXACTAMENTE todos los campos de los METADATOS (Asignatura, Estudios, Autores, Nivel_cognitivo_Bloom, Tipo_pregunta, Competencias_relacionadas, Nivel de dificultad, Tema / topic, Idioma). Estos valores serán idénticos para todas las preguntas de este lote.\n"
                        "REGLA CRÍTICA DE EXTRACCIÓN: Los únicos campos que cambian en cada objeto JSON son 'Enunciado_completo' y 'Solución', los cuales debes extraer individualmente del texto del EXAMEN.\n"
                        "REGLA CRÍTICA PARA 'Curso': Lee el curso en los metadatos y elige únicamente entre: 'Primero', 'Segundo', 'Tercero', 'Cuarto'.\n"
                        "REGLA CRÍTICA, Si hay preguntas tipo test, selección múltiple... en el enunciado tiene que haber todas las posibles respuestas. ES OBLIGATORIO"
                    )
                },
                {
                    "role": "user",
                    "content": (
                        "Analiza los metadatos y el texto del examen, y genera la lista de JSONs con este formato:\n"
                        "[\n"
                        "  {\n"
                        '    "Asignatura": "... (Copiado de METADATOS)",\n'
                        '    "Curso": "Primero, Segundo, Tercero o Cuarto (Copiado de METADATOS)",\n'
                        '    "Estudios": "... (Copiado de METADATOS)",\n'
                        '    "Autores": "... (Copiado de METADATOS)",\n'
                        '    "Nivel_cognitivo_Bloom": "... (Copiado de METADATOS)",\n'
                        '    "Tipo_pregunta": "... (Copiado de METADATOS)",\n'
                        '    "Competencias_relacionadas": "... (Copiado de METADATOS)",\n'
                        '    "Nivel de dificultad": "... (Copiado de METADATOS)",\n'
                        '    "Tema / topic": "... (Copiado de METADATOS)",\n'
                        '    "Idioma": "... (Copiado de METADATOS)",\n'
                        '    "Enunciado_completo": "...Si es tipo test, pon las posibles respuestas en el enunciado (Extraído del EXAMEN para esta pregunta en concreto)",\n'
                        '    "Solución": "... (Extraído del EXAMEN para esta pregunta en concreto)"\n'
                        "  }\n"
                        "]\n\n"
                        f"--- METADATOS GLOBALES ---\n{texto_metadatos}\n\n"
                        f"--- TEXTO DEL EXAMEN (PREGUNTAS Y RESPUESTAS) ---\n{texto_del_documento}"
                    )
                }
            ]
            )

            campos_requeridos = {"Asignatura", "Curso", "Estudios", "Autores", "Nivel_cognitivo_Bloom", "Tipo_pregunta", "Competencias_relacionadas", "Nivel de dificultad", "Tema / topic", "Idioma", "Enunciado_completo", "Solución"}


        datos = comprobar_respuesta_llm(completion.choices[0].message.content, campos_requeridos)

        if datos is None:
            i -= 1
        else:
            print("🎉 ¡La IA ha acertado el formato!")
            break 

    if datos is None:
        print("⚠️ Se han agotado los 3 intentos. Abortando esta extracción.")
        return
    else:
        with open("src/data.json", "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=4)
        print("✅ Json guardado correctamente como copia de seguridad.")
        
        # Pasamos los datos directamente por la memoria a la siguiente función
        segunda_iteracion_llm(datos)


def segunda_iteracion_llm(datos):
    # ////////////////  Segunda iteracion sobre cada pregunta  ////////////////

    # leemos el fichero de competencias
    with open("src/competencias.json", 'r', encoding='utf-8') as file:
        competencias = json.load(file)


    # ////////////////  Conexion con MongoDB  ////////////////
    # creamos un nuevo cliente y nos conectamos al servidor
    clienteMongo = MongoClient(uri_mongo, server_api=ServerApi('1'))

    #  declaramos la base de datos y la coleccion
    db = clienteMongo["proyecto_alumno_colaborador"]
    coleccion = db["preguntas_examenes"]

    # recorremos la lista para rellenar los otros campos
    for elemento in datos:
        completion = cliente.chat.completions.create(
        extra_headers={},
        extra_body={},
        model=modelo_llm,
        messages = [
            {
                "role": "system",
                "content": (
                    "Eres un clasificador académico estricto. Tu tarea es analizar una pregunta y devolver EXCLUSIVAMENTE un único objeto JSON (no una lista).\n"
                    "REGLA DE FORMATO: Devuelve ÚNICAMENTE estas 3 claves. Está prohibido devolver la pregunta original o cualquier otra clave.\n"
                    "1. 'Nivel_cognitivo_Bloom': Analiza el verbo de la pregunta y elige uno de: 'Recordar', 'Entender', 'Aplicar', 'Analizar', 'Evaluar', 'Crear'.\n"
                    "2. 'Tipo_pregunta': Elige uno de: 'Resolución de problemas', 'Diseño/Modelado', 'Análisis de caso práctico', 'Test. Opción múltiple', 'Respuesta corta', 'Codificación', 'Ensayo breve'.\n"
                    "3. 'Competencias_detalladas': Analiza la clave 'Competencias_relacionadas' de la pregunta. Busca esos códigos en el CATÁLOGO DE COMPETENCIAS y devuelve una lista (array) con las descripciones completas que correspondan."
                )
            },
            {
                "role": "user",
                "content": (
                    "Genera el JSON con la clasificación para esta pregunta. Devuelve SOLO las 3 claves solicitadas en este formato:\n"
                    "{\n"
                    '  "Nivel_cognitivo_Bloom": "...",\n'
                    '  "Tipo_pregunta": "...",\n'
                    '  "Competencias_detalladas": ["descripción 1", "descripción 2"]\n'
                    "}\n\n"
                    "--- CATÁLOGO DE COMPETENCIAS ---\n"
                    f"{competencias}\n\n"
                    "--- PREGUNTA A ANALIZAR ---\n"
                    f"{json.dumps(elemento, ensure_ascii=False)}"
                )
            }
        ]
        )
        # pasamos la respuesta del llm a una lista de python
        respuesta_llm = procesar_respuesta_llm(completion.choices[0].message.content)


        if respuesta_llm and len(respuesta_llm) > 0:
            # Mezclamos el objeto original con el del LLM
            elemento.update(respuesta_llm)
            
            # 3. Guardamos el objeto COMPLETO
            coleccion.insert_one(elemento)
            print(f"✅ Guardado completo en MongoDB: {elemento.get('Asignatura')}")
        else:
            print("⚠️ El LLM no devolvió datos válidos para esta pregunta, saltando...")


if __name__ == "__main__":
    # fichero = "../ejemplos de preguntas/Sistemas Digitales/resueltas/SSDD_parcial1-2526_v2 - Solucions.docx"
    # fichero = "../ejemplos de preguntas/Fundamentos Computadores/05.562_20241_PAC1_Solució.pdf"
    # fichero = "../ejemplos de preguntas/programacion1/kahootLinks.txt"
    fichero = "src/kahootLinks.txt"
    ruta_fichero = Path(fichero) # convertimos la ruta del fichero a un path para obtener la extension

    # fichero de metadatos si hay
    fichero_metadatos = None 
    # fichero_metadatos = "../ejemplos de preguntas/programacion1/metainformacion preguntas.docx"
    fichero_metadatos = "src/metainformacion preguntas.docx"

    metadatos = False # variable para saber si hay documento de metadatos
    
    
    if fichero_metadatos != None:
        metadatos = True # hay metadatos
        ruta_fichero_metadatos = Path(fichero_metadatos)
        texto_metadatos = extraer_texto_docx(ruta_fichero_metadatos)


    # ////////////////  Obtener tipo de fichero  ////////////////
    match ruta_fichero.suffix:
        case ".docx":
            texto_del_documento = extraer_texto_docx(ruta_fichero)
            llamada_llm()
        case ".pdf":
            texto_del_documento = extraer_texto_pdf(ruta_fichero)
            llamada_llm()
        case ".txt":
            with open(ruta_fichero) as file:
                for line in file:
                    nombreKahoot = Path(line.rstrip()) # lo convertimos en una ruta para extraer el codigo
                    linkKahoot = url_kahoot + str(nombreKahoot.name) # concatenamos el link con el codigo del kahoot
                    respuesta = requests.get(linkKahoot) # hacemos una peticion a la url
                    texto_del_documento = respuesta.text # obtenemos el json con las preguntas
                    llamada_llm(metadatos)

