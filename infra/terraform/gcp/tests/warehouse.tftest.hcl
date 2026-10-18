# terraform test (no cloud account needed: providers are mocked).
mock_provider "google" {}

variables {
  project_id        = "d2d-test-project"
  dashboard_viewers = ["user:viewer@example.com"]
}

run "dataset_and_least_privilege_loader" {
  command = plan

  assert {
    condition     = google_bigquery_dataset.warehouse.dataset_id == "d2d_analytics"
    error_message = "Default dataset is d2d_analytics (analytics/bigquery/load.sh)."
  }
  assert {
    condition     = google_bigquery_dataset_iam_member.loader_editor.role == "roles/bigquery.dataEditor"
    error_message = "The loader edits this dataset only."
  }
  assert {
    condition     = google_project_iam_member.loader_jobs.role == "roles/bigquery.jobUser"
    error_message = "The loader only needs to run jobs at project level."
  }
  assert {
    condition     = [for m in google_bigquery_dataset_iam_member.viewers : m.role] == ["roles/bigquery.dataViewer"]
    error_message = "Dashboard viewers get read-only access."
  }
  assert {
    condition     = google_bigquery_dataset.warehouse.delete_contents_on_destroy == false
    error_message = "Destroying the stack must not silently delete loaded tables."
  }
}

run "rejects_invalid_dataset_id" {
  command = plan
  variables {
    dataset_id = "d2d-analytics"
  }
  expect_failures = [var.dataset_id]
}
