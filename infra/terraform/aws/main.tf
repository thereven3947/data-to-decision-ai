# Off-site copy of the nightly Postgres backups (deploy/backup.sh keeps 14 local dumps).
# The bucket is private, encrypted, versioned and TLS-only; the uploader can only add objects under
# its prefixes and can neither read, list nor delete them, so a compromised Mac mini cannot erase
# the off-site history. Create the uploader's access key outside Terraform (keys in state are
# secrets at rest): aws iam create-access-key --user-name <uploader_user>.

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "backups" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_ownership_controls" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    id     = "tier-and-expire"
    status = "Enabled"
    filter {}

    transition {
      days          = var.glacier_after_days
      storage_class = "GLACIER_IR"
    }
    expiration {
      days = var.expire_after_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.backups]
}

data "aws_iam_policy_document" "tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.backups.arn,
      "${aws_s3_bucket.backups.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "backups" {
  bucket     = aws_s3_bucket.backups.id
  policy     = data.aws_iam_policy_document.tls_only.json
  depends_on = [aws_s3_bucket_public_access_block.backups]
}

resource "aws_iam_user" "uploader" {
  name = "d2d-backup-uploader"
  path = "/d2d/"
}

data "aws_iam_policy_document" "uploader" {
  statement {
    sid       = "PutBackupsOnly"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = [for env in var.environments : "${aws_s3_bucket.backups.arn}/${env}/*"]
  }
}

resource "aws_iam_user_policy" "uploader" {
  name   = "d2d-backup-put-only"
  user   = aws_iam_user.uploader.name
  policy = data.aws_iam_policy_document.uploader.json
}
