output "backup_uri" {
  description = "Set D2D_BACKUP_S3_URI to this value (plus /<env>) in the environment .env."
  value       = "s3://${aws_s3_bucket.backups.bucket}"
}

output "uploader_user" {
  description = "IAM user allowed to PutObject only; create its access key out of band."
  value       = aws_iam_user.uploader.name
}

output "account_id" {
  value = data.aws_caller_identity.current.account_id
}
