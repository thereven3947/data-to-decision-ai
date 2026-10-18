# terraform test (no cloud account needed: providers are mocked; needs Terraform >= 1.11).
mock_provider "aws" {}

variables {
  bucket_name = "d2d-backups-123456789012"
}

# The bucket ARN is computed by AWS; pin it so policies can be checked at plan time.
override_resource {
  target          = aws_s3_bucket.backups
  override_during = plan
  values = {
    id     = "d2d-backups-123456789012"
    bucket = "d2d-backups-123456789012"
    arn    = "arn:aws:s3:::d2d-backups-123456789012"
  }
}

run "bucket_is_private_versioned_and_encrypted" {
  command = plan

  assert {
    condition     = aws_s3_bucket_versioning.backups.versioning_configuration[0].status == "Enabled"
    error_message = "Backups must be versioned."
  }
  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.backups.block_public_acls,
      aws_s3_bucket_public_access_block.backups.block_public_policy,
      aws_s3_bucket_public_access_block.backups.ignore_public_acls,
      aws_s3_bucket_public_access_block.backups.restrict_public_buckets,
    ])
    error_message = "All public access must be blocked."
  }
  assert {
    condition     = one([for r in aws_s3_bucket_server_side_encryption_configuration.backups.rule : r.apply_server_side_encryption_by_default[0].sse_algorithm]) == "AES256"
    error_message = "Backups must be encrypted at rest."
  }
  assert {
    condition = flatten([
      for s in data.aws_iam_policy_document.tls_only.statement : [
        for c in s.condition : [for v in c.values : "${c.variable}=${v}"]
      ] if s.effect == "Deny"
    ]) == ["aws:SecureTransport=false"]
    error_message = "Requests without TLS must be denied."
  }
}

run "uploader_can_only_put_under_env_prefixes" {
  command = plan

  assert {
    condition     = toset(flatten(data.aws_iam_policy_document.uploader.statement[*].actions)) == toset(["s3:PutObject"])
    error_message = "The uploader must not read, list or delete."
  }
  assert {
    condition = toset(flatten(data.aws_iam_policy_document.uploader.statement[*].resources)) == toset([
      "arn:aws:s3:::d2d-backups-123456789012/production/*",
      "arn:aws:s3:::d2d-backups-123456789012/staging/*",
    ])
    error_message = "The uploader may only write under its environment prefixes."
  }
}

run "rejects_invalid_bucket_name" {
  command = plan
  variables {
    bucket_name = "Not_A_Bucket"
  }
  expect_failures = [var.bucket_name]
}

run "rejects_expiry_before_tiering" {
  command = plan
  variables {
    glacier_after_days = 90
    expire_after_days  = 60
  }
  expect_failures = [var.expire_after_days]
}
