# Dummy deployment package placeholder for Terraform validation/planning
data "archive_file" "lambda_dummy_zip" {
  type        = "zip"
  output_path = "${path.module}/dataset_processor_placeholder.zip"

  source {
    content  = "# Placeholder package for Lambda deployment\ndef lambda_handler(event, context):\n    pass\n"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "dataset_processor" {
  function_name = "${var.project_name}-dataset-processor-${var.environment}"
  description   = "Event-driven dataset profiling and semantic schema extraction engine"

  filename         = data.archive_file.lambda_dummy_zip.output_path
  source_code_hash = data.archive_file.lambda_dummy_zip.output_base64sha256

  runtime = "python3.11"
  handler = "lambdas.dataset_processor.handler.lambda_handler"
  role    = aws_iam_role.lambda_exec.arn

  memory_size = var.lambda_memory_size
  timeout     = var.lambda_timeout

  environment {
    variables = {
      LOCAL_MODE     = "false"
      AWS_REGION     = var.aws_region
      AWS_S3_BUCKET  = aws_s3_bucket.veridex_data.id
      DATABASE_URL   = "postgresql+psycopg://${var.db_username}:${random_password.db_password.result}@${aws_db_instance.veridex_postgres.endpoint}/${var.db_name}"
      LOG_LEVEL      = "INFO"
      LOG_FORMAT     = "json"
    }
  }

  tags = {
    Name = "${var.project_name}-dataset-processor"
  }
}
