# Random master password generation for RDS PostgreSQL
resource "random_password" "db_password" {
  length           = 24
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

# Store password securely in AWS SSM Parameter Store
resource "aws_ssm_parameter" "db_password" {
  name        = "/${var.project_name}/${var.environment}/database/password"
  description = "VERIDEX PostgreSQL Master Password"
  type        = "SecureString"
  value       = random_password.db_password.result
}

# Security group for RDS PostgreSQL
resource "aws_security_group" "rds" {
  name        = "${var.project_name}-rds-sg-${var.environment}"
  description = "Allow inbound PostgreSQL traffic from VERIDEX API service"

  ingress {
    description = "PostgreSQL ingress"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"] # Restricted to internal VPC range
  }

  egress {
    description = "Egress all"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-rds-sg"
  }
}

# Production RDS PostgreSQL Instance
resource "aws_db_instance" "veridex_postgres" {
  identifier        = "${var.project_name}-rds-${var.environment}"
  engine            = "postgres"
  engine_version    = "16.3"
  instance_class    = var.db_instance_class
  allocated_storage = var.db_allocated_storage
  storage_type      = "gp3"

  db_name  = var.db_name
  username = var.db_username
  password = random_password.db_password.result

  storage_encrypted   = true
  publicly_accessible = false
  skip_final_snapshot = true

  vpc_security_group_ids = [aws_security_group.rds.id]

  # Maintenance & backup windows
  backup_retention_period = 7
  backup_window           = "03:00-04:00"
  maintenance_window      = "Sun:04:30-Sun:05:30"
  auto_minor_version_upgrade = true

  tags = {
    Name = "${var.project_name}-postgres"
  }
}
