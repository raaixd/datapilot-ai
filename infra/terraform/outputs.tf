output "s3_bucket_name" {
  description = "Name of the provisioned VERIDEX data and artifact S3 bucket"
  value       = aws_s3_bucket.veridex_data.id
}

output "s3_bucket_arn" {
  description = "ARN of the provisioned VERIDEX S3 bucket"
  value       = aws_s3_bucket.veridex_data.arn
}

output "rds_endpoint" {
  description = "Connection endpoint for the RDS PostgreSQL instance"
  value       = aws_db_instance.veridex_postgres.endpoint
}

output "rds_database_name" {
  description = "PostgreSQL metadata database name"
  value       = aws_db_instance.veridex_postgres.db_name
}

output "lambda_function_name" {
  description = "Name of the dataset processor Lambda function"
  value       = aws_lambda_function.dataset_processor.function_name
}

output "lambda_function_arn" {
  description = "ARN of the dataset processor Lambda function"
  value       = aws_lambda_function.dataset_processor.arn
}

output "cloudwatch_api_log_group" {
  description = "Name of the CloudWatch log group for the API service"
  value       = aws_cloudwatch_log_group.api_service.name
}

output "cloudwatch_lambda_log_group" {
  description = "Name of the CloudWatch log group for the Lambda processor"
  value       = aws_cloudwatch_log_group.lambda_processor.name
}
