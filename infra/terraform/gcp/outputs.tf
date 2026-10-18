output "dataset" {
  description = "Fully qualified dataset for analytics/bigquery/load.sh."
  value       = "${var.project_id}:${google_bigquery_dataset.warehouse.dataset_id}"
}

output "loader_service_account" {
  description = "Impersonate this account to load: gcloud config set auth/impersonate_service_account <email>."
  value       = google_service_account.loader.email
}
