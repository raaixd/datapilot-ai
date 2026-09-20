resource "random_id" "bucket_suffix" {
  byte_length = 4
}

locals {
  bucket_name = var.s3_bucket_name != "" ? var.s3_bucket_name : "${var.project_name}-data-${var.environment}-${random_id.bucket_suffix.hex}"
}

resource "aws_s3_bucket" "veridex_data" {
  bucket        = locals.bucket_name
  force_destroy = false
}

# Block all public access by default
resource "aws_s3_bucket_public_access_block" "veridex_data" {
  bucket = aws_s3_bucket.veridex_data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Enable bucket versioning for auditability and recovery
resource "aws_s3_bucket_versioning" "veridex_data" {
  bucket = aws_s3_bucket.veridex_data.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Free server-side encryption at rest using AES256
resource "aws_s3_bucket_server_side_encryption_configuration" "veridex_data" {
  bucket = aws_s3_bucket.veridex_data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Lifecycle rules to manage storage costs
resource "aws_s3_bucket_lifecycle_configuration" "veridex_data" {
  bucket = aws_s3_bucket.veridex_data.id

  rule {
    id     = "expire-old-versions"
    status = "Enabled"

    filter {
      prefix = ""
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# Event notification triggering Lambda processor on raw uploads
resource "aws_s3_bucket_notification" "dataset_uploaded" {
  bucket = aws_s3_bucket.veridex_data.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.dataset_processor.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "raw/"
  }

  depends_on = [aws_lambda_permission.allow_s3_invocation]
}
