provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "VERIDEX"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}
