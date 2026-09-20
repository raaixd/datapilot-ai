"""
AWS Lambda Dataset Processor.

Event-driven processor triggered by S3 ObjectCreated events or direct invocation:
  1. Parses S3 object key: `raw/{project_id}/{dataset_id}/{filename}`
  2. Enforces idempotency via checksum & processing state checks
  3. Validates file format and loads DataFrame
  4. Profiles the dataset deterministically (DataProfiler)
  5. Extracts semantic schema & data types (SemanticSchemaGenerator)
  6. Stores profile.json and semantic_schema.json back into S3
  7. Updates dataset and column records in RDS PostgreSQL (or local DB in development)
  8. Transitions dataset state to READY (or FAILED on error)

Follows the official AWS S3 -> Lambda pattern:
https://docs.aws.amazon.com/lambda/latest/dg/with-s3-example.html
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from typing import Any

from app.services.ingestion import IngestionService

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def parse_s3_key(key: str) -> tuple[str, str, str]:
    """Extract project_id, dataset_id, and filename from an S3 raw key.

    Expected format: `raw/{project_id}/{dataset_id}/{filename}`
    """
    decoded_key = urllib.parse.unquote_plus(key)
    parts = decoded_key.split("/", 3)
    if len(parts) != 4 or parts[0] != "raw":
        raise ValueError(
            f"Invalid S3 raw key format '{decoded_key}'. Expected 'raw/{{project_id}}/{{dataset_id}}/{{filename}}'."
        )
    return parts[1], parts[2], parts[3]


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Lambda entrypoint for S3 ObjectCreated events and direct invocation."""
    logger.info('{"event": "lambda_invocation_started", "payload_type": "%s"}', type(event).__name__)

    service = IngestionService()
    results = []

    # Case 1: Standard S3 Event Notification
    if "Records" in event:
        for record in event["Records"]:
            # Only process S3 event records
            if record.get("eventSource") == "aws:s3":
                s3_info = record["s3"]
                key = s3_info["object"]["key"]
                bucket = s3_info["bucket"]["name"]

                try:
                    project_id, dataset_id, filename = parse_s3_key(key)
                    logger.info(
                        '{"event": "processing_s3_record", "bucket": "%s", "key": "%s", "project_id": "%s", "dataset_id": "%s"}',
                        bucket,
                        key,
                        project_id,
                        dataset_id,
                    )
                    dataset = service.process_dataset(project_id=project_id, dataset_id=dataset_id)
                    results.append(
                        {
                            "dataset_id": dataset_id,
                            "status": dataset.processing_status,
                            "rows": dataset.row_count,
                            "cols": dataset.column_count,
                        }
                    )
                except Exception as exc:
                    logger.error(
                        '{"event": "record_processing_failed", "key": "%s", "error": "%s"}',
                        key,
                        str(exc),
                    )
                    results.append({"key": key, "status": "FAILED", "error": str(exc)})

    # Case 2: Direct invocation payload (e.g. from local dispatcher or step function)
    elif "project_id" in event and "dataset_id" in event:
        project_id = event["project_id"]
        dataset_id = event["dataset_id"]
        logger.info(
            '{"event": "direct_invocation", "project_id": "%s", "dataset_id": "%s"}',
            project_id,
            dataset_id,
        )
        dataset = service.process_dataset(project_id=project_id, dataset_id=dataset_id)
        results.append(
            {
                "dataset_id": dataset_id,
                "status": dataset.processing_status,
                "rows": dataset.row_count,
                "cols": dataset.column_count,
            }
        )

    else:
        logger.warning('{"event": "unrecognized_event_payload", "keys": %s}', list(event.keys()))
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Unrecognized event format. Expected S3 notification or project_id/dataset_id."}),
        }

    return {
        "statusCode": 200,
        "body": json.dumps({"processed_count": len(results), "results": results}),
    }
