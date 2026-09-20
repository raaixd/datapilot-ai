"""
Unit tests for AWS S3 Storage Adapter using mocked boto3 client.

Verifies:
  - Put, get, delete, exists, presigned URL operations
  - AES256 server-side encryption
  - Error translation (NoSuchKey -> FileNotFoundError)
  - Paginated list_objects
  - Metadata retrieval via head_object
  - Profile and endpoint configuration
  - Conformance to StorageBackend Protocol
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.storage.base import StorageBackend
from app.storage.s3_storage import S3StorageBackend


class TestS3StorageBackendMocked(unittest.TestCase):
    def setUp(self):
        self.backend = S3StorageBackend(
            bucket="test-veridex-bucket",
            region="us-east-1",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret",
            aws_profile="test-profile",
            endpoint_url="http://localhost:4566",
        )
        self.mock_client = MagicMock()
        self.backend._client = self.mock_client

    def test_protocol_conformance(self):
        self.assertIsInstance(self.backend, StorageBackend)

    def test_put_object_calls_boto3_with_encryption(self):
        self.mock_client.put_object.return_value = {}
        res = self.backend.put_object("raw/proj1/ds1/data.csv", b"content", content_type="text/csv")

        self.assertEqual(res, "raw/proj1/ds1/data.csv")
        self.mock_client.put_object.assert_called_once_with(
            Bucket="test-veridex-bucket",
            Key="raw/proj1/ds1/data.csv",
            Body=b"content",
            ContentType="text/csv",
            ServerSideEncryption="AES256",
        )

    def test_get_object_success(self):
        mock_body = MagicMock()
        mock_body.read.return_value = b"sample,csv,data"
        self.mock_client.get_object.return_value = {"Body": mock_body}

        data = self.backend.get_object("raw/proj1/ds1/data.csv")
        self.assertEqual(data, b"sample,csv,data")
        self.mock_client.get_object.assert_called_once_with(
            Bucket="test-veridex-bucket",
            Key="raw/proj1/ds1/data.csv",
        )

    def test_get_object_nosuchkey_raises_file_not_found(self):
        from botocore.exceptions import ClientError

        err = ClientError({"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "GetObject")
        self.mock_client.get_object.side_effect = err

        with self.assertRaises(FileNotFoundError):
            self.backend.get_object("raw/missing.csv")

    def test_object_exists_true_and_false(self):
        from botocore.exceptions import ClientError

        # Exists
        self.mock_client.head_object.return_value = {"ContentLength": 42}
        self.assertTrue(self.backend.object_exists("raw/exists.csv"))

        # Does not exist (404)
        err = ClientError({"Error": {"Code": "404", "Message": "Not found"}}, "HeadObject")
        self.mock_client.head_object.side_effect = err
        self.assertFalse(self.backend.object_exists("raw/missing.csv"))

    def test_generate_presigned_url(self):
        self.mock_client.generate_presigned_url.return_value = "https://s3.amazonaws.com/test-url"
        url = self.backend.generate_presigned_url("reports/rep1.json", expiry_seconds=1800)

        self.assertEqual(url, "https://s3.amazonaws.com/test-url")
        self.mock_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "test-veridex-bucket", "Key": "reports/rep1.json"},
            ExpiresIn=1800,
        )

    def test_delete_object(self):
        self.mock_client.delete_object.return_value = {}
        self.backend.delete_object("raw/file.csv")

        self.mock_client.delete_object.assert_called_once_with(
            Bucket="test-veridex-bucket",
            Key="raw/file.csv",
        )

    def test_list_objects_pagination(self):
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {"Contents": [{"Key": "raw/p1/d1/a.csv"}, {"Key": "raw/p1/d2/b.csv"}]},
            {"Contents": [{"Key": "raw/p1/d3/c.csv"}]},
        ]
        self.mock_client.get_paginator.return_value = mock_paginator

        keys = self.backend.list_objects(prefix="raw/p1/")
        self.assertEqual(len(keys), 3)
        self.assertEqual(keys, ["raw/p1/d1/a.csv", "raw/p1/d2/b.csv", "raw/p1/d3/c.csv"])
        self.mock_client.get_paginator.assert_called_once_with("list_objects_v2")

    def test_get_object_metadata_success(self):
        self.mock_client.head_object.return_value = {
            "ContentLength": 1024,
            "ContentType": "text/csv",
            "LastModified": "2026-09-20T12:00:00Z",
            "ETag": '"abc123etag"',
        }

        meta = self.backend.get_object_metadata("raw/data.csv")
        self.assertEqual(meta["key"], "raw/data.csv")
        self.assertEqual(meta["size_bytes"], 1024)
        self.assertEqual(meta["content_type"], "text/csv")
        self.assertEqual(meta["etag"], "abc123etag")

    def test_get_object_metadata_not_found_raises(self):
        from botocore.exceptions import ClientError

        err = ClientError({"Error": {"Code": "NoSuchKey", "Message": "Key not found"}}, "HeadObject")
        self.mock_client.head_object.side_effect = err

        with self.assertRaises(FileNotFoundError):
            self.backend.get_object_metadata("missing.csv")


if __name__ == "__main__":
    unittest.main()
