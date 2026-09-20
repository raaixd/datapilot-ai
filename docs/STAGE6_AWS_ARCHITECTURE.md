# VERIDEX Stage 6: AWS Cloud-Native Architecture & Event-Driven Ingestion

## 1. Overview & Architectural Philosophy

VERIDEX is engineered as an enterprise-grade, cloud-native AI Data Analyst platform built around AWS primitives. The platform adheres strictly to two complementary operational models:

1. **Local Development Mode (`LOCAL_MODE=true`):**
   - **Cost:** $0.00 AWS spend.
   - **Dependencies:** Standard local storage filesystem emulation, SQLite for application metadata, DuckDB/SQLite for query execution, and Mock or local LLM providers.
   - **Network:** Zero outbound calls to AWS services (S3, RDS, CloudWatch, Lambda).

2. **Cloud-Native Production Mode (`LOCAL_MODE=false`):**
   - **Storage:** Amazon Simple Storage Service (S3) with server-side encryption (`AES256`) and structured object prefix hierarchy (`raw/`, `processed/`, `metadata/`, `reports/`).
   - **Compute & Ingestion:** AWS Lambda triggered asynchronously via S3 `ObjectCreated` event notifications for deterministic data profiling and semantic schema extraction.
   - **Relational Metadata:** Amazon Relational Database Service (RDS) running PostgreSQL with connection pooling (`pool_pre_ping`, `pool_recycle`), read replicas, and automated backups.
   - **Analytical Execution:** Isolated in-memory/embedded columnar analytics database executing verified, read-only SQL queries.
   - **Telemetry:** Amazon CloudWatch Logs with structured JSON formatting and metric emission.
   - **Security:** Least-privilege AWS Identity and Access Management (IAM) roles for API tasks and Lambda executions.

---

## 2. Target AWS Production Architecture

```
                    ┌─────────────────────────┐
                    │  VERIDEX User Console   │
                    │   (Streamlit / Web)     │
                    └────────────┬────────────┘
                                 │ HTTPS (REST API)
                                 ▼
                    ┌─────────────────────────┐
                    │    FastAPI Backend      │
                    │ (ECS Fargate / AppRunner)│
                    └────────────┬────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         │                       │                       │
         ▼                       ▼                       ▼
  ┌──────────────┐        ┌──────────────┐        ┌──────────────┐
  │  Amazon S3   │        │   AWS RDS    │        │  AI Analyst  │
  │ Object Store │        │  PostgreSQL  │        │ Orchestrator │
  └──────┬───────┘        └──────▲───────┘        └──────┬───────┘
         │                       │                       │
         │ s3:ObjectCreated:*    │ Updates metadata:     ▼
         ▼                       │ READY / FAILED   Verified SQL
  ┌──────────────┐               │                  & Execution
  │  AWS Lambda  ├───────────────┘
  │ (Processor)  │
  └──────┬───────┘
         │ Writes:
         ├──► metadata/{project_id}/{dataset_id}/profile.json
         └──► metadata/{project_id}/{dataset_id}/semantic_schema.json

  ┌────────────────────────────────────────────────────────┐
  │              Amazon CloudWatch Logs & Metrics          │
  │ (Structured JSON logs, latency percentiles, error rates)│
  └────────────────────────────────────────────────────────┘
```

---

## 3. Service Responsibilities

### A. FastAPI Backend Service (API Layer)
- Serves REST endpoints (`/projects`, `/datasets`, `/analyses`, `/reports`, `/metrics`, `/health`, `/ready`).
- Authenticates users and isolates tenant workspaces.
- Handles dataset upload: streams incoming files directly to S3 (`raw/{project_id}/{dataset_id}/{filename}`) and records an initial `UPLOADED` database entry.
- Dispatches analytical queries to the AI Analyst engine and returns verified SQL, results previews, and grounded insights.
- Emits structured operational metrics (`request_count`, `error_rate`, `latency_ms`).

### B. Amazon S3 (Object Storage Layer)
- Serves as the central data lake and artifact repository.
- Stores raw uploaded datasets with strict lifecycle management.
- Stores deterministic profiling metadata (`profile.json`) and semantic schemas (`semantic_schema.json`).
- Stores compiled analytical reports (`reports/{project_id}/{analysis_id}/report.json`).
- Configured with `ServerSideEncryption = AES256`, bucket versioning, and public access block.

### C. AWS Lambda (Dataset Processor)
- Event-driven compute component executing on Python 3.11 runtime.
- Automatically invoked when new files land in `raw/` via S3 Event Notifications.
- Executes deterministic data profiling (`DataProfiler`) and semantic schema extraction (`SemanticSchemaGenerator`) without calling paid LLM APIs.
- Updates dataset status in PostgreSQL: `UPLOADED` -> `VALIDATING` -> `PROCESSING` -> `READY` (or `FAILED`).
- Enforces strict idempotency: skips duplicate processing if the file checksum has already been processed into a `READY` state.

### D. AWS RDS PostgreSQL (Metadata Repository)
- High-availability relational database storing structured entity models:
  - `projects`: workspace metadata and descriptions.
  - `datasets`: filename, S3 storage keys, checksums, status, row/column counts.
  - `columns`: semantic type inferences, physical dtypes, null percentages, statistical distributions.
  - `analyses` & `analysis_queries`: natural language questions, verified SQL, repair counts, runtime latencies.
  - `reports`: 10-section analytical briefing documents, summaries, and Markdown exports.
- Governed by connection pooling with `pool_pre_ping=True`, `pool_recycle=1800`, and automatic transaction rollback.

### E. Amazon CloudWatch (Observability Layer)
- Centralized log aggregation for FastAPI and Lambda execution logs.
- Ingests structured JSON events containing `request_id`, `dataset_id`, `analysis_id`, `latency_ms`, and `error_code`.
- Configured with 30-day log retention to prevent unbounded operational costs.

---

## 4. Storage Key Prefix Convention

All object keys across local simulation and production S3 follow a deterministic schema:

```
raw/{project_id}/{dataset_id}/{filename}
processed/{project_id}/{dataset_id}/normalized.parquet
metadata/{project_id}/{dataset_id}/profile.json
metadata/{project_id}/{dataset_id}/semantic_schema.json
reports/{project_id}/{analysis_id}/report.json
```

This strict hierarchy ensures that IAM policies can restrict Lambda and API access down to specific prefix boundaries without granting blanket bucket permissions.

---

## 5. Event Flow & Ingestion Lifecycle

```
1. User Uploads CSV/XLSX
          │
          ▼
2. FastAPI stores object:
   s3://bucket/raw/{project_id}/{dataset_id}/{filename}
          │
          ▼
3. S3 fires Event Notification:
   s3:ObjectCreated:Put ──► AWS Lambda (dataset_processor)
                                  │
          ┌───────────────────────┴───────────────────────┐
          │                                               │
          ▼                                               ▼
   [Download Raw Bytes]                            [Checksum Check]
          │                                               │
          ▼                                               ▼
   [Load DataFrame]                                (If already READY:
          │                                         exit idempotently)
          ▼
   [Deterministic DataProfiler]
          │
          ▼
   [SemanticSchemaGenerator]
          │
          ├──► Put s3://bucket/metadata/.../profile.json
          ├──► Put s3://bucket/metadata/.../semantic_schema.json
          │
          ▼
   [Update PostgreSQL]
   - Column profiles inserted
   - Status set to 'READY'
```

---

## 6. IAM Least-Privilege Security Model

To prevent privilege escalation and protect data integrity:

### Lambda Execution Role (`veridex_lambda_exec_role`)
- **S3 Read:** Allowed `s3:GetObject` ONLY on `arn:aws:s3:::bucket/raw/*`.
- **S3 Write:** Allowed `s3:PutObject` ONLY on `arn:aws:s3:::bucket/metadata/*` and `arn:aws:s3:::bucket/processed/*`.
- **CloudWatch Logs:** Allowed `logs:CreateLogStream`, `logs:PutLogEvents` on `/aws/lambda/veridex-dataset-processor`.
- **No Access:** Cannot delete objects, cannot read reports, cannot access other AWS resources.

### API Service Role (`veridex_api_role`)
- **S3 Read/Write:** Allowed `s3:PutObject` on `raw/*` and `reports/*`; allowed `s3:GetObject` across all bucket prefixes to serve API requests.
- **RDS Access:** Network security group ingress to RDS port 5432; IAM database authentication where enabled.
- **CloudWatch Logs:** Allowed `logs:PutLogEvents` on `/veridex/api`.

---

## 7. Cost Considerations & Boundaries

- **Local Mode:** Incurs **$0.00**.
- **Production Serverless Design:**
  - **S3:** Standard storage for raw files (< $0.05/month for typical analytical datasets).
  - **Lambda:** Free tier covers 1M requests/month and 3.2M seconds of compute time.
  - **RDS:** Sized at `db.t4g.micro` or `db.t4g.small` with single-AZ in development, expandable to Multi-AZ in production.
  - **CloudWatch:** Log retention capped at 30 days to prevent ongoing storage charges.
  - **No NAT Gateways or ALB provisioned** during testing or development stages.
