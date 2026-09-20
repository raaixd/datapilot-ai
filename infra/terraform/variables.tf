variable "aws_region" {
  description = "AWS region for all provisioned cloud resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment (e.g. dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name prefix used for resource naming"
  type        = string
  default     = "veridex"
}

variable "s3_bucket_name" {
  description = "Name for the primary VERIDEX S3 data and artifact bucket"
  type        = string
  default     = ""
}

variable "db_name" {
  description = "PostgreSQL database name for metadata repository"
  type        = string
  default     = "veridex"
}

variable "db_username" {
  description = "PostgreSQL master username"
  type        = string
  default     = "veridex_admin"
}

variable "db_instance_class" {
  description = "RDS instance compute class"
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "Initial storage allocated for PostgreSQL instance in GB"
  type        = number
  default     = 20
}

variable "lambda_memory_size" {
  description = "Memory size allocated for dataset processor Lambda function in MB"
  type        = number
  default     = 512
}

variable "lambda_timeout" {
  description = "Timeout for dataset processor Lambda function in seconds"
  type        = number
  default     = 300
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 30
}
