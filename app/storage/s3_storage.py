"""
AWS S3 storage backend.

Uses boto3 to interact with S3. Authentication order (boto3 default chain):
  1. IAM instance/task/execution role — preferred in AWS (Lambda, ECS, EC2)
  2. AWS_PROFILE named profile (if configured in Settings / environment)
  3. AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY environment variables
  4. ~/.aws/credentials file

NEVER commit credentials to source control.
ALWAYS prefer IAM roles when running inside AWS.

The S3 bucket name is read from AWS_S3_BUCKET (see app/core/config.py).
The region is read from AWS_REGION (default: us-east-1).

DESIGN DECISIONS:
- The boto3 client is lazily initialised so that tests that never call
  S3 operations don't need boto3 installed.
- ServerSideEncryption=AES256 is applied to every PUT for data-at-rest
  protection (free, no KMS cost).
- Presigned URLs use the S3 client's generate_presigned_url, which
  creates time-limited temporary access without exposing credentials.
- ClientError is re-raised with a normalised FileNotFoundError for
  missing keys, so callers don't need boto3 exception knowledge.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class S3StorageBackend:
    """StorageBackend implementation backed by AWS S3.

    Used when LOCAL_MODE=false (production).
    Satisfies the StorageBackend Protocol without inheriting from it.
    """

    def __init__(
        self,
        bucket: str,
        region: str = "us-east-1",
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        aws_profile: str | None = None,
        endpoint_url: str | None = None,
    ):
        if not bucket:
            raise ValueError(
                "AWS_S3_BUCKET is not set. Configure it in your .env (LOCAL_MODE=false requires a real S3 bucket)."
            )
        self._bucket = bucket
        self._region = region
        self._access_key = aws_access_key_id
        self._secret_key = aws_secret_access_key
        self._profile = aws_profile
        self._endpoint_url = endpoint_url
        self._client: Any = None
        logger.info("S3StorageBackend initialised (bucket=%s, region=%s)", bucket, region)

    def _get_client(self) -> Any:
        """Lazy-initialise the boto3 S3 client."""
        if self._client is None:
            try:
                import boto3  # type: ignore[import]
            except ImportError as exc:
                raise ImportError(
                    "The 'boto3' package is required for S3StorageBackend. "
                    "Install it with `pip install boto3`."
                ) from exc

            session_kwargs: dict[str, Any] = {}
            if self._profile:
                session_kwargs["profile_name"] = self._profile

            session = boto3.Session(**session_kwargs) if session_kwargs else boto3

            client_kwargs: dict[str, Any] = {"region_name": self._region}
            if self._endpoint_url:
                client_kwargs["endpoint_url"] = self._endpoint_url
            if self._access_key and self._secret_key:
                client_kwargs["aws_access_key_id"] = self._access_key
                client_kwargs["aws_secret_access_key"] = self._secret_key
                logger.warning(
                    "Using explicit AWS credentials. Prefer IAM roles when running inside AWS."
                )

            self._client = session.client("s3", **client_kwargs)
        return self._client

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        client = self._get_client()
        try:
            client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                ServerSideEncryption="AES256",  # Data at rest encryption (free)
            )
            logger.debug("S3 PUT s3://%s/%s bytes=%d", self._bucket, key, len(data))
            return key
        except Exception as exc:
            logger.error("S3 PUT failed key=%s: %s", key, type(exc).__name__)
            raise

    def get_object(self, key: str) -> bytes:
        client = self._get_client()
        try:
            response = client.get_object(Bucket=self._bucket, Key=key)
            data = response["Body"].read()
            logger.debug("S3 GET s3://%s/%s bytes=%d", self._bucket, key, len(data))
            return data
        except Exception as exc:
            err_code = getattr(getattr(exc, "response", {}), "get", lambda *a: None)(
                "Error", {}
            ).get("Code", "")
            if err_code in ("NoSuchKey", "404") or "NoSuchKey" in str(exc):
                raise FileNotFoundError(f"No S3 object found at key '{key}'.") from exc
            logger.error("S3 GET failed key=%s: %s", key, type(exc).__name__)
            raise

    def object_exists(self, key: str) -> bool:
        client = self._get_client()
        try:
            client.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception as exc:
            err_code = ""
            try:
                err_code = exc.response["Error"]["Code"]  # type: ignore[attr-defined]
            except (AttributeError, KeyError, TypeError):
                pass
            if err_code in ("404", "NoSuchKey"):
                return False
            logger.warning("S3 head_object failed key=%s: %s", key, type(exc).__name__)
            return False

    def generate_presigned_url(self, key: str, expiry_seconds: int = 3600) -> str:
        client = self._get_client()
        try:
            url = client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expiry_seconds,
            )
            logger.debug("S3 presigned URL generated key=%s expiry=%ds", key, expiry_seconds)
            return url
        except Exception as exc:
            logger.error("S3 presigned URL failed key=%s: %s", key, type(exc).__name__)
            raise

    def delete_object(self, key: str) -> None:
        client = self._get_client()
        try:
            client.delete_object(Bucket=self._bucket, Key=key)
            logger.debug("S3 DELETE s3://%s/%s", self._bucket, key)
        except Exception as exc:
            logger.warning("S3 DELETE failed key=%s: %s", key, type(exc).__name__)

    def list_objects(self, prefix: str = "") -> list[str]:
        """List all object keys matching prefix using pagination."""
        client = self._get_client()
        keys: list[str] = []
        try:
            paginator = client.get_paginator("list_objects_v2")
            clean_prefix = prefix.lstrip("/")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=clean_prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
            return keys
        except Exception as exc:
            logger.error("S3 list_objects failed prefix=%s: %s", prefix, type(exc).__name__)
            raise

    def get_object_metadata(self, key: str) -> dict[str, Any]:
        """Retrieve metadata for the S3 object."""
        client = self._get_client()
        try:
            resp = client.head_object(Bucket=self._bucket, Key=key)
            return {
                "key": key,
                "size_bytes": resp.get("ContentLength", 0),
                "content_type": resp.get("ContentType", "application/octet-stream"),
                "last_modified": resp.get("LastModified"),
                "etag": resp.get("ETag", "").strip('"'),
            }
        except Exception as exc:
            err_code = getattr(getattr(exc, "response", {}), "get", lambda *a: None)(
                "Error", {}
            ).get("Code", "")
            if err_code in ("NoSuchKey", "404") or "NoSuchKey" in str(exc) or "404" in str(exc):
                raise FileNotFoundError(f"No S3 object found at key '{key}'.") from exc
            logger.error("S3 get_object_metadata failed key=%s: %s", key, type(exc).__name__)
            raise
