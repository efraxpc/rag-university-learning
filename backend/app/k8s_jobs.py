"""Creación del Job de chunking vía la API de Kubernetes.

Sigue el patrón del tutorial oficial de Google Cloud (endpoint.py crea un
batch/v1 Job por documento): pods Spot, backoffLimit=3, TTL tras finalizar,
bucket montado con el GCS FUSE CSI driver.
https://docs.cloud.google.com/kubernetes-engine/docs/tutorials/build-rag-chatbot
"""

import uuid

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from .config import settings

_loaded = False


def _load_config() -> None:
    global _loaded
    if _loaded:
        return
    try:
        config.load_incluster_config()  # dentro de GKE
    except config.ConfigException:
        config.load_kube_config()  # desarrollo local
    _loaded = True


def create_chunker_job(document_id: int, object_name: str) -> str:
    """Crea el Job que vectoriza un documento. Devuelve el nombre del Job."""
    _load_config()
    name = f"chunker-{document_id}-{uuid.uuid4().hex[:6]}"

    env = [
        client.V1EnvVar(name="DOCUMENT_ID", value=str(document_id)),
        client.V1EnvVar(name="BUCKET_NAME", value=settings.bucket_name),
        client.V1EnvVar(name="FILE_NAME", value=object_name),
        client.V1EnvVar(name="INSTANCE_CONNECTION_NAME", value=settings.instance_connection_name),
        client.V1EnvVar(name="DB_NAME", value=settings.db_name),
        client.V1EnvVar(name="DB_USER", value=settings.db_user),
        client.V1EnvVar(name="EMBEDDING_MODEL", value=settings.embedding_model),
        client.V1EnvVar(name="EMBEDDING_DIMS", value=str(settings.embedding_dims)),
        client.V1EnvVar(name="SMALL_TO_BIG", value=str(settings.small_to_big).lower()),
        client.V1EnvVar(name="PARENT_CHUNK_SIZE", value=str(settings.parent_chunk_size)),
        client.V1EnvVar(name="PARENT_CHUNK_OVERLAP", value=str(settings.parent_chunk_overlap)),
        client.V1EnvVar(name="SMALL_CHUNK_SIZE", value=str(settings.small_chunk_size)),
        client.V1EnvVar(name="SMALL_CHUNK_OVERLAP", value=str(settings.small_chunk_overlap)),
        client.V1EnvVar(name="GOOGLE_GENAI_USE_VERTEXAI", value=settings.google_genai_use_vertexai),
        client.V1EnvVar(name="PROJECT_ID", value=settings.project_id),
        client.V1EnvVar(name="REGION", value=settings.region),
        client.V1EnvVar(
            name="DB_PASS",
            value_from=client.V1EnvVarSource(
                secret_key_ref=client.V1SecretKeySelector(name="rag-secrets", key="DB_PASS")
            ),
        ),
        client.V1EnvVar(
            name="GEMINI_API_KEY",
            value_from=client.V1EnvVarSource(
                secret_key_ref=client.V1SecretKeySelector(name="rag-secrets", key="GEMINI_API_KEY")
            ),
        ),
    ]

    container = client.V1Container(
        name="chunker",
        image=settings.chunker_image,
        env=env,
        volume_mounts=[
            client.V1VolumeMount(name="docs", mount_path="/documents", read_only=True)
        ],
        resources=client.V1ResourceRequirements(
            requests={"cpu": "500m", "memory": "512Mi"}
        ),
    )

    # Bucket montado con el GCS FUSE CSI driver (solo lectura).
    fuse_volume = client.V1Volume(
        name="docs",
        csi=client.V1CSIVolumeSource(
            driver="gcsfuse.csi.storage.gke.io",
            read_only=True,
            volume_attributes={"bucketName": settings.bucket_name},
        ),
    )

    pod_spec = client.V1PodSpec(
        containers=[container],
        volumes=[fuse_volume],
        restart_policy="Never",
        service_account_name="ksa-chunker",
        node_selector={"cloud.google.com/gke-spot": "true"},  # Spot: −60–91 %
    )

    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(name=name, namespace=settings.job_namespace),
        spec=client.V1JobSpec(
            backoff_limit=3,
            ttl_seconds_after_finished=300,
            template=client.V1PodTemplateSpec(spec=pod_spec),
        ),
    )

    try:
        client.BatchV1Api().create_namespaced_job(settings.job_namespace, job)
    except ApiException as exc:
        raise RuntimeError(f"No se pudo crear el Job de chunking: {exc}") from exc
    return name
