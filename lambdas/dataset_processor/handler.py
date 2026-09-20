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
    if not key or not isinstance(key, str):
        raise ValueError("Invalid S3 raw key: key must be a non-empty string.")

    decoded_key = urllib.parse.unquote_plus(key)
    parts = decoded_key.split("/", 3)
    if len(parts) != 4 or parts[0] != "raw" or not parts[1] or not parts[2] or not parts[3]:
        raise ValueError(
            f"Invalid S3 raw key format '{decoded_key}'. Expected 'raw/{{project_id}}/{{dataset_id}}/{{filename}}'."
        )
    return parts[1], parts[2], parts[3]


def lambda_handler(event: dict[str, Any] | Any, context: Any = None) -> dict[str, Any]:
    """Lambda entrypoint for S3 ObjectCreated events and direct invocation.

    Returns:
        A dictionary with statusCode and JSON body summarizing processing outcomes.
    """
    if not isinstance(event, dict):
        logger.warning('{"event": "invalid_event_type", "type": "%s"}', type(event).__name__)
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Event payload must be a JSON object", "error_code": "MALFORMED_EVENT"}),
        }

    logger.info('{"event": "lambda_invocation_started", "payload_keys": %s}', list(event.keys()))

    service = IngestionService()
    results: list[dict[str, Any]] = []

    # Case 1: Standard S3 Event Notification
    if "Records" in event:
        records = event.get("Records")
        if not isinstance(records, list) or len(records) == 0:
            logger.warning('{"event": "empty_or_invalid_records_list"}')
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "'Records' must be a non-empty list", "error_code": "MALFORMED_EVENT"}),
            }

        for record in records:
            if not isinstance(record, dict):
                results.append({"status": "FAILED", "error": "Malformed record", "error_code": "MALFORMED_RECORD"})
                continue

            # Only process S3 event records
            if record.get("eventSource") == "aws:s3":
                s3_info = record.get("s3")
                if not isinstance(s3_info, dict):
                    results.append({"status": "FAILED", "error": "Missing S3 block", "error_code": "MISSING_S3_BLOCK"})
                    continue

                bucket_info = s3_info.get("bucket", {})
                object_info = s3_info.get("object", {})
                key = object_info.get("key")
                bucket = bucket_info.get("name")

                if not bucket or not key:
                    results.append({"status": "FAILED", "error": "Missing bucket or key", "error_code": "MISSING_BUCKET_OR_KEY"})
                    continue

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
                except ValueError as val_err:
                    logger.error('{"event": "invalid_s3_key", "key": "%s", "error": "%s"}', key, str(val_err))
                    results.append({"key": key, "status": "FAILED", "error": str(val_err), "error_code": "INVALID_KEY"})
                except Exception as exc:
                    logger.error(
                        '{"event": "record_processing_failed", "key": "%s", "error": "%s"}',
                        key,
                        str(exc),
                    )
                    results.append({"key": key, "status": "FAILED", "error": str(exc), "error_code": "PROCESSING_ERROR"})
            else:
                logger.info('{"event": "skipped_non_s3_record", "source": "%s"}', record.get("eventSource"))

    # Case 2: Direct invocation payload (e.g. from local dispatcher or step function)
    elif "project_id" in event and "dataset_id" in event:
        project_id = str(event["project_id"]).strip()
        dataset_id = str(event["dataset_id"]).strip()

        if not project_id or not dataset_id:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "project_id and dataset_id must be non-empty strings", "error_code": "INVALID_PARAMETERS"}),
            }

        logger.info(
            '{"event": "direct_invocation", "project_id": "%s", "dataset_id": "%s"}',
            project_id,
            dataset_id,
        )
        try:
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
            logger.error('{"event": "direct_invocation_failed", "error": "%s"}', str(exc))
            return {
                "statusCode": 500,
                "body": json.dumps({"error": str(exc), "error_code": "PROCESSING_ERROR", "dataset_id": dataset_id}),
            }

    else:
        logger.warning('{"event": "unrecognized_event_payload", "keys": %s}', list(event.keys()))
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": "Unrecognized event format. Expected S3 notification or project_id/dataset_id.",
                "error_code": "UNRECOGNIZED_EVENT",
            }),
        }

    return {
        "statusCode": 200,
        "body": json.dumps({"processed_count": len(results), "results": results}),
    }
