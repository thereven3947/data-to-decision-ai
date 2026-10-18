variable "project_id" {
  description = "GCP project that holds the analytics dataset."
  type        = string
}

variable "location" {
  description = "BigQuery dataset location."
  type        = string
  default     = "US"
}

variable "dataset_id" {
  description = "BigQuery dataset for the warehouse export (analytics/bigquery/load.sh)."
  type        = string
  default     = "d2d_analytics"

  validation {
    condition     = can(regex("^[A-Za-z_][A-Za-z0-9_]*$", var.dataset_id)) && length(var.dataset_id) <= 1024
    error_message = "dataset_id must be letters, digits and underscores."
  }
}

variable "dashboard_viewers" {
  description = "Principals that may read the dataset from Looker Studio, e.g. [\"user:me@example.com\"]."
  type        = list(string)
  default     = []
}

variable "table_expiration_days" {
  description = "Days before a loaded table expires (0 keeps tables until replaced)."
  type        = number
  default     = 0
}
