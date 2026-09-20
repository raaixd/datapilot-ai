data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# =============================================================================
# 1. Lambda Dataset Processor Execution Role
# =============================================================================

resource "aws_iam_role" "lambda_exec" {
  name = "${var.project_name}-lambda-exec-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

# Least-privilege policy for Lambda
resource "aws_iam_policy" "lambda_policy" {
  name        = "${var.project_name}-lambda-policy-${var.environment}"
  description = "Least-privilege policy for VERIDEX Lambda dataset processor"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Read raw uploads only
      {
        Sid    = "S3ReadRawOnly"
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = "${aws_s3_bucket.veridex_data.arn}/raw/*"
      },
      # Write profiling metadata and normalized data only
      {
        Sid    = "S3WriteMetadataAndProcessed"
        Effect = "Allow"
        Action = [
          "s3:PutObject"
        ]
        Resource = [
          "${aws_s3_bucket.veridex_data.arn}/metadata/*",
          "${aws_s3_bucket.veridex_data.arn}/processed/*"
        ]
      },
      # CloudWatch Logs emission
      {
        Sid    = "CloudWatchLogging"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.lambda_processor.arn}:*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_attach" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = aws_iam_policy.lambda_policy.arn
}

# Allow S3 bucket to trigger Lambda function
resource "aws_lambda_permission" "allow_s3_invocation" {
  statement_id  = "AllowExecutionFromS3Bucket"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dataset_processor.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.veridex_data.arn
}

# =============================================================================
# 2. FastAPI Backend Service Execution Role
# =============================================================================

resource "aws_iam_role" "api_service" {
  name = "${var.project_name}-api-role-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = [
            "ecs-tasks.amazonaws.com",
            "apprunner.amazonaws.com"
          ]
        }
      }
    ]
  })
}

# Least-privilege policy for API backend
resource "aws_iam_policy" "api_policy" {
  name        = "${var.project_name}-api-policy-${var.environment}"
  description = "Least-privilege policy for VERIDEX FastAPI backend service"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # List bucket contents
      {
        Sid    = "S3ListBucket"
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = aws_s3_bucket.veridex_data.arn
      },
      # Upload raw datasets and compiled reports
      {
        Sid    = "S3WriteUploadsAndReports"
        Effect = "Allow"
        Action = [
          "s3:PutObject"
        ]
        Resource = [
          "${aws_s3_bucket.veridex_data.arn}/raw/*",
          "${aws_s3_bucket.veridex_data.arn}/reports/*"
        ]
      },
      # Read stored datasets and metadata for analysis
      {
        Sid    = "S3ReadStoredData"
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = "${aws_s3_bucket.veridex_data.arn}/*"
      },
      # CloudWatch Logs emission
      {
        Sid    = "CloudWatchLogging"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.api_service.arn}:*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "api_attach" {
  role       = aws_iam_role.api_service.name
  policy_arn = aws_iam_policy.api_policy.arn
}
