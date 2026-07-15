# Proyecto Alumno Colaborador: Extractor de Exámenes con IA

Este proyecto es una herramienta automatizada diseñada para extraer, procesar y catalogar preguntas de exámenes académicos (PDF, DOCX y enlaces de Kahoot) utilizando Inteligencia Artificial (Modelos LLM locales vía Ollama, o usando OpenRouter). 

El sistema guarda los documentos originales en un clúster de **MinIO** y persiste los datos estructurados y catalogados (Nivel de Bloom, Competencias, Asignatura...) en **MongoDB**, exponiendo todo el ecosistema a través de una API REST construida con **FastAPI**.

## Arquitectura del Sistema
El proyecto está completamente contenerizado usando Docker e incluye los siguientes servicios:
- **App (FastAPI):** Backend central en Python gestionado con `uv`.
- **MongoDB:** Base de datos NoSQL para el almacenamiento de preguntas procesadas.
- **MinIO:** Almacenamiento de objetos compatible con S3 para guardar los PDFs y DOCXs originales.
- **Ollama:** Servidor local de Modelos de Lenguaje Grande (LLMs).

---

## Requisitos Previos

1. [Docker](https://www.docker.com/) y Docker Compose instalados en tu máquina.
2. *(Opcional pero recomendado si se usa Ollama)* Una tarjeta gráfica NVIDIA para aceleración de la IA.

---

## Configuración y Puesta en Marcha

### 1. Variables de Entorno
Crea un archivo llamado `.env` en la raíz del proyecto basándote en el archivo de ejemplo.
    
    cp .env.example .env

Rellena los datos en tu nuevo archivo .env (credenciales de Mongo, MinIO, etc.).

### 1.1 Alternar entre IA Local y Nube (OpenRouter)
El sistema está preparado para usar modelos locales (gratis y privados) o modelos avanzados en la nube vía OpenRouter. Para alternar entre ellos, solo tienes que modificar la variable `LLM_PROVIDER` en tu archivo `.env`:

* **Para usar IA Local:** Configura `LLM_PROVIDER=ollama` y asegúrate de tener el modelo descargado en tu servidor de Ollama (ej. `OLLAMA_MODEL=llama3.2`).
* **Para usar OpenRouter:** Configura `LLM_PROVIDER=openrouter`. Necesitarás añadir tu API Key en la variable `OPENROUTER_API_KEY` y definir el modelo deseado en `OPENROUTER_MODEL`.

### 2. Ejecutar con CPU (Mac / Linux estándar)
Si no dispones de una GPU dedicada, levanta el proyecto utilizando el archivo base:
    
    docker compose up --build -d

### 3. Ejecutar con GPU Nvidia (Windows / Linux con GPU)
Para aprovechar la aceleración de hardware, asegúrate de que tu `.env` contiene esta línea que fusiona las configuraciones:
    
    COMPOSE_FILE=docker-compose.yml;docker-compose.gpu.yml

Luego ejecuta el mismo comando:
    
    docker compose up --build -d

---

## Uso de la API (Endpoints Principales)

Una vez que los contenedores estén en ejecución, el servidor estará disponible en el puerto `8000`. 

FastAPI genera automáticamente una documentación interactiva (Swagger UI). Puedes acceder a ella y probar todas las funcionalidades desde tu navegador en:
**[http://localhost:8000/docs](http://localhost:8000/docs)**

### Resumen de Rutas:
* `POST /procesar-documento`: Sube un archivo PDF/DOCX (y opcionalmente un archivo de metadatos) para extraer sus preguntas con IA.
* `POST /procesar-kahoot`: Envía un enlace de un Kahoot público para extraer e indexar sus preguntas.
* `GET /preguntas`: Consulta la base de datos de MongoDB. Permite aplicar filtros dinámicos por `asignatura`, `curso` y `nivel_bloom`, así como seleccionar que campos quieres visualizar.
* `GET /documentos/{nombre_archivo}`: Descarga un documento original almacenado previamente en MinIO.

## Tecnologías Utilizadas
* **Python 3.11+** (Gestionado con `uv`)
* **FastAPI & Uvicorn** (Servidor Web)
* **pdfplumber & python-docx** (Extracción de texto y tablas)
* **Pydantic** (Validación de datos)
* **Docker & Docker Compose**
