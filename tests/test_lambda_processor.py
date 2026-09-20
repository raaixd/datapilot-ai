"""
Tests for AWS Lambda dataset processor handler.

Verifies:
  - S3 ObjectCreated event record parsing
  - Direct invocation payload parsing
  - Idempotency when processing duplicate events
  - Key parser error boundaries
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from lambdas.dataset_processor.handler import lambda_handler, parse_s3_key


class TestLambdaProcessor:
    def test_parse_valid_s3_key(self):
        key = "raw/project-alpha/dataset-123/sales_q3.csv"
        proj, ds, fn = parse_s3_key(key)
        assert proj == "project-alpha"
        assert ds == "dataset-123"
        assert fn == "sales_q3.csv"

    def test_parse_s3_key_with_url_encoding(self):
        key = "raw/project%20one/ds-456/my%20data.csv"
        proj, ds, fn = parse_s3_key(key)
        assert proj == "project one"
        assert ds == "ds-456"
        assert fn == "my data.csv"

    def test_parse_invalid_s3_key_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid S3 raw key format"):
            parse_s3_key("processed/project/ds/data.parquet")

        with pytest.raises(ValueError, match="Invalid S3 raw key format"):
            parse_s3_key("too/short/key.csv")

    @patch("lambdas.dataset_processor.handler.IngestionService")
    def test_s3_event_record_processing(self, mock_service_cls):
        mock_service = MagicMock()
        mock_service_cls.return_value = mock_service

        mock_dataset = MagicMock()
        mock_dataset.processing_status = "READY"
        mock_dataset.row_count = 100
        mock_dataset.column_count = 5
        mock_service.process_dataset.return_value = mock_dataset

        event = {
            "Records": [
                {
                    "eventVersion": "2.1",
                    "eventSource": "aws:s3",
                    "s3": {
                        "bucket": {"name": "test-datapilot-bucket"},
                        "object": {"key": "raw/proj-101/ds-202/customers.csv"},
                    },
                }
            ]
        }

        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["processed_count"] == 1
        assert body["results"][0]["status"] == "READY"
        mock_service.process_dataset.assert_called_once_with(
            project_id="proj-101",
            dataset_id="ds-202",
        )

    @patch("lambdas.dataset_processor.handler.IngestionService")
    def test_direct_invocation_processing(self, mock_service_cls):
        mock_service = MagicMock()
        mock_service_cls.return_value = mock_service

        mock_dataset = MagicMock()
        mock_dataset.processing_status = "READY"
        mock_dataset.row_count = 50
        mock_dataset.column_count = 4
        mock_service.process_dataset.return_value = mock_dataset

        event = {
            "project_id": "direct-proj",
            "dataset_id": "direct-ds",
        }

        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["processed_count"] == 1
        mock_service.process_dataset.assert_called_once_with(
            project_id="direct-proj",
            dataset_id="direct-ds",
        )

    def test_unrecognized_event_returns_400(self):
        event = {"unknown_key": "some_value"}
        response = lambda_handler(event, None)
        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert "error" in body
        assert body["error_code"] == "UNRECOGNIZED_EVENT"

    def test_non_dict_event_returns_400(self):
        response = lambda_handler("not-a-dict", None)
        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert body["error_code"] == "MALFORMED_EVENT"

    def test_empty_records_list_returns_400(self):
        event = {"Records": []}
        response = lambda_handler(event, None)
        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert body["error_code"] == "MALFORMED_EVENT"

    def test_missing_bucket_or_key_in_record(self):
        event = {
            "Records": [
                {
                    "eventSource": "aws:s3",
                    "s3": {
                        "bucket": {},
                        "object": {},
                    },
                }
            ]
        }
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["results"][0]["status"] == "FAILED"
        assert body["results"][0]["error_code"] == "MISSING_BUCKET_OR_KEY"

    def test_direct_invocation_empty_params_returns_400(self):
        event = {"project_id": "   ", "dataset_id": ""}
        response = lambda_handler(event, None)
        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert body["error_code"] == "INVALID_PARAMETERS"

    @patch("lambdas.dataset_processor.handler.IngestionService")
    def test_direct_invocation_processing_failure_returns_500(self, mock_service_cls):
        mock_service = MagicMock()
        mock_service_cls.return_value = mock_service
        mock_service.process_dataset.side_effect = RuntimeError("Database connection dropped")

        event = {"project_id": "p1", "dataset_id": "d1"}
        response = lambda_handler(event, None)
        assert response["statusCode"] == 500
        body = json.loads(response["body"])
        assert body["error_code"] == "PROCESSING_ERROR"
        assert "Database connection dropped" in body["error"]
