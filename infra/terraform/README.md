# VERIDEX Infrastructure as Code (Terraform)

This directory contains the Terraform configuration defining the target cloud-native AWS infrastructure for the **VERIDEX AI Data Analyst Platform**.

> [!WARNING]
> **Stage 6 Safety Rule**:
> Do **NOT** run `terraform apply` during Stage 6 development. No live AWS resources are provisioned in this stage.

---

## Infrastructure Architecture

The configuration provisions:
1. **Amazon S3 (`s3.tf`):** Secure data lake bucket with private access block, versioning, AES256 server-side encryption, lifecycle expiration for old versions, and an S3 Event Notification triggering Lambda on `raw/` uploads.
2. **AWS IAM (`iam.tf`):** Strict least-privilege roles for the dataset processor Lambda function and FastAPI backend service.
3. **AWS RDS PostgreSQL (`rds.tf`):** Dedicated metadata repository instance (`db.t4g.micro`, 20GB gp3, encrypted storage, automated backups).
4. **AWS Lambda (`lambda.tf`):** Serverless Python 3.11 dataset processor executing deterministic data profiling and semantic schema extraction.
5. **Amazon CloudWatch (`cloudwatch.tf`):** Structured log groups for the API service and Lambda function with 30-day retention policies.

---

## Local Validation Instructions

To validate the Terraform configuration syntax locally without connecting to AWS:

```bash
cd infra/terraform

# 1. Initialize Terraform plugins (downloads AWS provider)
terraform init

# 2. Check formatting
terraform fmt -check

# 3. Validate configuration syntax and resource schemas
terraform validate
```

---

## Planning (Dry-Run Only)

To generate an execution plan showing what resources would be provisioned:

```bash
# Set your AWS credentials/profile
export AWS_REGION="us-east-1"
export AWS_PROFILE="your-aws-profile"

# Run a dry-run plan
terraform plan
```

> **REMINDER:** Never run `terraform apply` without explicit approval and confirmed budget allocation.
