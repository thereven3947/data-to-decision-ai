variable "region" {
  description = "AWS region for the off-site backup bucket."
  type        = string
  default     = "us-west-2"
}

variable "bucket_name" {
  description = "Globally unique bucket name, e.g. d2d-backups-<account-id>."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "bucket_name must be a valid S3 bucket name."
  }
}

variable "environments" {
  description = "Environments whose nightly Postgres dumps are copied off-site (deploy/backup.sh)."
  type        = list(string)
  default     = ["production", "staging"]
}

variable "glacier_after_days" {
  description = "Move backups to Glacier Instant Retrieval after this many days."
  type        = number
  default     = 30
}

variable "expire_after_days" {
  description = "Delete current backups after this many days (noncurrent versions follow 30 days later)."
  type        = number
  default     = 365

  validation {
    condition     = var.expire_after_days > var.glacier_after_days
    error_message = "expire_after_days must be greater than glacier_after_days."
  }
}
