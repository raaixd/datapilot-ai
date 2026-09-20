"""
Storage backend factory.

Returns a StorageBackend instance based on the LOCAL_MODE setting:
  - LOCAL_MODE=true  → LocalStorageBackend (filesystem, zero AWS cost)
  - LOCAL_MODE=false → S3StorageBackend    (real AWS S3)

Called once at application startup (app/api/main.py) and injected
into the pipeline and API layer. Tests always receive a LocalStorageBackend
pointed at a temporary directory.

WHY THIS SEPARATION EXISTS:
The rest of the application (pipeline, API, Lambda handler) only imports
from app/storage/base.py and calls methods on the returned backend. Swapping
LOCAL_MODE=true → false in .env changes the entire storage layer without
any application code changes — this is the "interface + adapter" pattern
required by the target architecture spec.
"""

from __future__ import annotations

import logging

from app.storage.base import StorageBackend

logger = logging.getLogger(__name__)


def build_storage_backend(
    local_mode: bool = True,
    local_storage_root: str = "data/local_s3",
    aws_s3_bucket: str = "",
    aws_region: str = "us-east-1",
    aws_access_key_id: str | None = None,
    aws_secret_access_key: str | None = None,
) -> StorageBackend:
    """Build and return the appropriate storage backend.

    Args:
        local_mode:          True for local filesystem (development).
        local_storage_root:  Root directory for local storage.
        aws_s3_bucket:       S3 bucket name (production only).
        aws_region:          AWS region (production only).
        aws_access_key_id:   Explicit AWS key (prefer IAM roles instead).
        aws_secret_access_key: Explicit AWS secret (prefer IAM roles instead).

    Returns:
        A StorageBackend instance (LocalStorageBackend or S3StorageBackend).
    """
    if local_mode:
        from app.storage.local_storage import LocalStorageBackend

        backend = LocalStorageBackend(root=local_storage_root)
        logger.info("Storage: LocalStorageBackend (root=%s)", local_storage_root)
        return backend  # type: ignore[return-value]

    from app.storage.s3_storage import S3StorageBackend

    backend = S3StorageBackend(
        bucket=aws_s3_bucket,
        region=aws_region,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )
    logger.info("Storage: S3StorageBackend (bucket=%s, region=%s)", aws_s3_bucket, aws_region)
    return backend  # type: ignore[return-value]


def build_storage_backend_from_settings(settings=None) -> StorageBackend:
    """Convenience wrapper: build backend from a Settings object."""
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    return build_storage_backend(
        local_mode=settings.local_mode,
        local_storage_root=settings.local_storage_root,
        aws_s3_bucket=settings.aws_s3_bucket,
        aws_region=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )
