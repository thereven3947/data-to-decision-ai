# BigQuery side of the analytics warehouse (docs/analytics-warehouse.md):
# a private dataset, a loader service account with the least privilege load.sh needs, and read-only
# access for dashboard viewers. No service-account key is created here: keys in Terraform state are
# secrets at rest. Use `gcloud auth login` or impersonation of the loader account instead.

resource "google_project_service" "bigquery" {
  service            = "bigquery.googleapis.com"
  disable_on_destroy = false
}

resource "google_bigquery_dataset" "warehouse" {
  dataset_id    = var.dataset_id
  friendly_name = "D2D analytics warehouse"
  description   = "Star-schema export of D2D change intelligence (d2d export warehouse)."
  location      = var.location

  default_table_expiration_ms = var.table_expiration_days > 0 ? var.table_expiration_days * 86400000 : null
  delete_contents_on_destroy  = false

  labels = {
    app       = "d2d"
    component = "analytics"
  }

  depends_on = [google_project_service.bigquery]
}

resource "google_service_account" "loader" {
  account_id   = "d2d-warehouse-loader"
  display_name = "D2D warehouse loader"
  description  = "Loads the D2D Parquet export and refreshes the KPI views."
}

# Write access to this dataset only, not the whole project.
resource "google_bigquery_dataset_iam_member" "loader_editor" {
  dataset_id = google_bigquery_dataset.warehouse.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.loader.email}"
}

# Running load and query jobs is a project-level permission.
resource "google_project_iam_member" "loader_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.loader.email}"
}

resource "google_bigquery_dataset_iam_member" "viewers" {
  for_each   = toset(var.dashboard_viewers)
  dataset_id = google_bigquery_dataset.warehouse.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = each.value
}
