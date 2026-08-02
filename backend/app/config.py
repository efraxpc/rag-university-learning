"""Configuración del orquestador FastAPI.

Todas las variables se inyectan por entorno (manifiestos K8s + Secret Manager).
En desarrollo local se pueden exportar a mano o usar un fichero .env.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env en la raíz del repo (backend/app/config.py → parents[2]). Se lee
# independientemente del directorio desde el que se lance uvicorn.
# Precedencia (pydantic-settings): variable de entorno real > .env.
ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT_ENV, ".env"), extra="ignore")

    # GCP
    project_id: str = ""
    region: str = "us-central1"

    # Cloud SQL (vía conector, IP privada). En local usar DATABASE_URL
    # apuntando a cloud-sql-proxy (postgresql+pg8000://...@127.0.0.1:5432/ragdb).
    instance_connection_name: str = ""
    db_name: str = "ragdb"
    db_user: str = "app"
    db_pass: str = ""  # secret: db-password
    database_url: str = ""

    # Cloud Storage. Vacío = MODO LOCAL (sin GCP): los documentos se guardan
    # en disco y el chunker se ejecuta como subproceso local.
    bucket_name: str = ""
    local_docs_dir: str = "./local-docs"

    # Gemini API SOLO para embeddings (Anthropic no tiene modelo de
    # embeddings): AI Studio (free tier) con API key en local; Vertex AI si
    # GOOGLE_GENAI_USE_VERTEXAI=true, usando Workload Identity/ADC.
    gemini_api_key: str = ""  # secret: gemini-api-key
    google_genai_use_vertexai: str = "false"
    embedding_model: str = "text-embedding-001"
    # Dimensión de salida de los embeddings (Matryoshka). Debe coincidir con
    # vector(N) de scripts/init_db.sql. OJO: pgvector limita los índices
    # HNSW/IVFFlat a 2000 dims → 1536 es el máximo seguro recomendado.
    embedding_dims: int = 1536

    # Generación: Anthropic Claude con dos proveedores intercambiables
    # (patrón Strategy, ver app/llm.py):
    # - "anthropic" (DEFAULT): API directa de Anthropic; requiere
    #   ANTHROPIC_API_KEY (https://console.anthropic.com/).
    # - "vertex": Vertex AI Model Garden (SDK anthropic[vertex]);
    #   autenticación por ADC en local (gcloud auth application-default
    #   login) / Workload Identity en GKE, y el modelo habilitado en
    #   Model Garden del proyecto.
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""  # secret: anthropic-api-key (solo provider=anthropic)
    anthropic_vertex_region: str = "global"  # Fable 5: endpoint global/us/eu
    gen_model: str = "claude-fable-5"
    # Modelo de conocimiento general: complementa la respuesta RAG cuando el
    # tema SÍ está en los documentos y responde solo cuando NO está. Si el
    # modelo no está disponible se cae a gen_model (ver routers/query.py).
    general_model: str = "claude-fable-5"
    # Modelo auxiliar barato para llamadas de volumen: query rewrite/expansion
    # y el map paralelo de resúmenes (el reduce usa gen_model).
    fast_model: str = "claude-haiku-4-5"

    # RAG
    top_k: int = 4
    # Umbral de distancia coseno para decidir si los documentos responden a la
    # pregunta: si el mejor match la supera, se considera que la respuesta NO
    # está en el RAG y se contesta solo con conocimiento general.
    max_distance: float = 0.6
    # 4096: las explicaciones estructuradas con código necesitan más tokens.
    max_output_tokens: int = 4096
    # Presupuesto mayor para las llamadas que producen el RESUMEN FINAL de
    # clase (caso 1 grupo y los reduces): fusionan resúmenes largos y deben
    # dejar sitio a la sección "Ejemplo de código"; con 4096 la respuesta se
    # truncaba a media frase antes de llegar a ella.
    summary_max_output_tokens: int = 8192

    # Resumen de clase entera (map-reduce por metadatos, ver rag.py).
    # Tamaño de bloque del map en caracteres (~6-8k tokens: seguro y rápido
    # por llamada con FAST_MODEL) y paralelismo del map.
    summary_block_chars: int = 24000
    summary_max_workers: int = 4

    # Small-to-big (optimización pre-retrieval) + ventana deslizante.
    # Se propagan al Job de chunking / subproceso local.
    small_to_big: bool = True
    parent_chunk_size: int = 1024
    parent_chunk_overlap: int = 128
    small_chunk_size: int = 256
    small_chunk_overlap: int = 50

    # Optimizaciones pre-retrieval del lado de la consulta:
    # query rewriting (reescribir la pregunta con el LLM) y query expansion
    # multi-query (buscar con N variantes parafraseadas y fusionar).
    query_rewrite: bool = True
    query_expansion: bool = True
    expansion_variants: int = 3

    # Job de chunking
    chunker_image: str = ""
    job_namespace: str = "rag"

    # API
    cors_origins: str = "*"


settings = Settings()
