output "instance_connection_name" {
  description = "Connection name de Cloud SQL (project:region:instance)"
  value       = google_sql_database_instance.postgres.connection_name
}

output "bucket_name" {
  value = google_storage_bucket.docs.name
}

output "cluster_name" {
  value = google_container_cluster.rag.name
}

output "repo_url" {
  description = "Repo de Artifact Registry para las imágenes"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "gcp_sa_email" {
  value = google_service_account.rag.email
}

output "get_credentials_command" {
  value = "gcloud container clusters get-credentials ${var.cluster_name} --region ${var.region} --project ${var.project_id}"
}
