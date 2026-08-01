variable "project_id" {
  description = "ID del proyecto de GCP"
  type        = string
}

variable "region" {
  description = "Región de GCP para todos los recursos"
  type        = string
  default     = "us-central1"
}

variable "cluster_name" {
  description = "Nombre del clúster GKE Autopilot"
  type        = string
  default     = "rag-cluster"
}

variable "db_instance_name" {
  description = "Nombre de la instancia Cloud SQL"
  type        = string
  default     = "rag-postgres"
}

variable "db_name" {
  description = "Nombre de la base de datos de la aplicación"
  type        = string
  default     = "ragdb"
}

variable "db_user" {
  description = "Usuario de la aplicación en Cloud SQL"
  type        = string
  default     = "app"
}

variable "bucket_name" {
  description = "Nombre del bucket de documentos (globalmente único)"
  type        = string
}

variable "gemini_api_key" {
  description = "API key de Gemini (AI Studio). Se guarda en Secret Manager."
  type        = string
  sensitive   = true
}

variable "anthropic_api_key" {
  description = "API key de Anthropic (API directa, LLM_PROVIDER=anthropic). Se guarda en Secret Manager. Vacío si se usa LLM_PROVIDER=vertex."
  type        = string
  sensitive   = true
  default     = ""
}
