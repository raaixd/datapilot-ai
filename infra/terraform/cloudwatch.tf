# CloudWatch Log Group for FastAPI Backend Service
resource "aws_cloudwatch_log_group" "api_service" {
  name              = "/${var.project_name}/api"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${var.project_name}-api-logs"
  }
}

# CloudWatch Log Group for Lambda Dataset Processor
resource "aws_cloudwatch_log_group" "lambda_processor" {
  name              = "/aws/lambda/${var.project_name}-dataset-processor-${var.environment}"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${var.project_name}-lambda-logs"
  }
}
