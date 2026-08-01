terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  apis = [
    "compute.googleapis.com",
    "container.googleapis.com",
    "sqladmin.googleapis.com",
    "servicenetworking.googleapis.com",
    "storage.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "cloudbuild.googleapis.com",
  ]
}

# ---------------------------------------------------------------- APIs
resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

# ---------------------------------------------------------------- Red (IP privada para Cloud SQL)
resource "google_compute_network" "vpc" {
  name                    = "rag-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "subnet" {
  name          = "rag-subnet"
  ip_cidr_range = "10.0.0.0/20"
  region        = var.region
  network       = google_compute_network.vpc.id
}

resource "google_compute_global_address" "private_ip_range" {
  name          = "rag-private-ip-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_vpc_connection" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_range.name]
}

# ---------------------------------------------------------------- Cloud SQL (PostgreSQL + pgvector)
resource "random_password" "db_password" {
  length  = 24
  special = false
}

resource "google_sql_database_instance" "postgres" {
  name             = var.db_instance_name
  database_version = "POSTGRES_16"
  region           = var.region
  # Aprendizaje: sin protección para permitir terraform destroy.
  deletion_protection = false
  depends_on          = [google_service_networking_connection.private_vpc_connection]

  settings {
    tier              = "db-f1-micro" # mínimo disciplinado
    availability_type = "ZONAL"       # sin HA
    disk_size         = 10
    disk_type         = "PD_SSD"

    ip_configuration {
      ipv4_enabled    = false # solo IP privada
      private_network = google_compute_network.vpc.id
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = false
      backup_retention_settings {
        retained_backups = 7
      }
    }

    insights_config {
      query_insights_enabled = true # Query Insights (observabilidad estándar)
    }
  }
}

resource "google_sql_database" "ragdb" {
  name     = var.db_name
  instance = google_sql_database_instance.postgres.name
}

resource "google_sql_user" "app" {
  name     = var.db_user
  instance = google_sql_database_instance.postgres.name
  password = random_password.db_password.result
}

# ---------------------------------------------------------------- Cloud Storage (documentos crudos)
resource "google_storage_bucket" "docs" {
  name                        = var.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true # aprendizaje: permite destroy con objetos
  depends_on                  = [google_project_service.apis]
}

# ---------------------------------------------------------------- GKE Autopilot (+ GCS FUSE CSI)
resource "google_container_cluster" "rag" {
  name             = var.cluster_name
  location         = var.region
  enable_autopilot = true

  network    = google_compute_network.vpc.id
  subnetwork = google_compute_subnetwork.subnet.id

  # Aprendizaje: permite terraform destroy.
  deletion_protection = false

  addons_config {
    gcs_fuse_csi_driver_config {
      enabled = true
    }
  }

  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------- Artifact Registry
resource "google_artifact_registry_repository" "images" {
  repository_id = "rag-images"
  location      = var.region
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

# ---------------------------------------------------------------- Secret Manager
resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = "gemini-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "gemini_api_key" {
  secret      = google_secret_manager_secret.gemini_api_key.id
  secret_data = var.gemini_api_key
}

resource "google_secret_manager_secret" "db_password" {
  secret_id = "db-password"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db_password.result
}

# ---------------------------------------------------------------- IAM / Workload Identity
# Una única cuenta de servicio de aplicación (mínimo viable; separar por
# workload es el camino de endurecimiento documentado).
resource "google_service_account" "rag" {
  account_id   = "gsa-rag"
  display_name = "RAG app (GKE workloads)"
  depends_on   = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "rag_object_user" {
  bucket = google_storage_bucket.docs.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.rag.email}"
}

resource "google_project_iam_member" "rag_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.rag.email}"
}

resource "google_secret_manager_secret_iam_member" "rag_secrets" {
  for_each = {
    gemini = google_secret_manager_secret.gemini_api_key.id
    db     = google_secret_manager_secret.db_password.id
  }
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.rag.email}"
}

# Workload Identity: las KSAs ksa-api y ksa-chunker (namespace rag) actúan como gsa-rag.
resource "google_service_account_iam_member" "workload_identity" {
  for_each           = toset(["ksa-api", "ksa-chunker"])
  service_account_id = google_service_account.rag.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[rag/${each.value}]"
}
