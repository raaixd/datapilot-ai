"""
Local filesystem storage backend.

Simulates S3's flat key-value store on the local filesystem, using the
key as a relative path under a configured root directory. This allows the
entire application to run without any AWS account or boto3 credentials —
LOCAL_MODE=true is the default for development.

The local key structure mirrors production:
    {local_storage_root}/raw/{project_id}/{dataset_id}/filename
    {local_storage_root}/metadata/{project_id}/{dataset_id}/profile.json
    ...

DESIGN DECISIONS:
- put_object is idempotent (overwrites existing keys), matching S3 semantics.
- object_exists checks only for file existence, not content validity.
- generate_presigned_url returns a file:// URI — valid in browsers and for
  download in Python, but NOT a real URL. Clearly marked as local-only.
- delete_object is a no-op if the file does not exist (S3 semantics).
- Thread safety: individual reads/writes on modern OS filesystems are
  effectively atomic at the OS level for small files. For the access
  patterns in DataPilot (one write per upload, reads during analysis),
  no additional locking is required.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class LocalStorageBackend:
    """StorageBackend implementation backed by the local filesystem.

    Used when LOCAL_MODE=true (the default for development).
    Satisfies the StorageBackend Protocol without inheriting from it.
    """

    def __init__(self, root: str = "data/local_s3"):
        """Args:
            root: Root directory for the local storage tree. Created if absent.
        """
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        logger.info("LocalStorageBackend initialised (root=%s)", self._root.resolve())

    def _resolve(self, key: str) -> Path:
        """Resolve a storage key to an absolute local path.

        Raises ValueError if the key resolves outside the root (path traversal guard).
        """
        path = (self._root / key).resolve()
        if not str(path).startswith(str(self._root.resolve())):
            raise ValueError(f"Storage key '{key}' resolves outside root — rejected.")
        return path

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        logger.debug("LocalStorage PUT key=%s bytes=%d", key, len(data))
        return key

    def get_object(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.exists():
            raise FileNotFoundError(f"No object found at storage key '{key}'.")
        data = path.read_bytes()
        logger.debug("LocalStorage GET key=%s bytes=%d", key, len(data))
        return data

    def object_exists(self, key: str) -> bool:
        try:
            path = self._resolve(key)
            return path.exists() and path.is_file()
        except ValueError:
            return False

    def generate_presigned_url(self, key: str, expiry_seconds: int = 3600) -> str:
        """Return a file:// URI for local access.

        WARNING: This is a development-only URL. It does not expire and
        is not accessible over a network. Use only for local testing.
        """
        path = self._resolve(key)
        if not path.exists():
            raise FileNotFoundError(f"Cannot generate URL for missing key '{key}'.")
        uri = path.as_uri()  # file:///abs/path/to/file
        logger.debug("LocalStorage presigned URL (local only) key=%s", key)
        return uri

    def delete_object(self, key: str) -> None:
        try:
            path = self._resolve(key)
            if path.exists():
                path.unlink()
                logger.debug("LocalStorage DELETE key=%s", key)
        except (ValueError, OSError) as exc:
            logger.warning("LocalStorage DELETE failed key=%s: %s", key, exc)

    def list_keys_with_prefix(self, prefix: str) -> list[str]:
        """List all keys under a given prefix (useful for tests and dev tooling)."""
        clean_prefix = prefix.strip("/").replace("/", os.sep)
        prefix_path = self._root / clean_prefix if clean_prefix else self._root
        if not prefix_path.exists():
            return []
        if prefix_path.is_file():
            return [str(prefix_path.relative_to(self._root)).replace(os.sep, "/")]
        return [
            str(p.relative_to(self._root)).replace(os.sep, "/")
            for p in prefix_path.rglob("*")
            if p.is_file()
        ]

    def list_objects(self, prefix: str = "") -> list[str]:
        """List all object keys matching the given prefix."""
        return self.list_keys_with_prefix(prefix)

    def get_object_metadata(self, key: str) -> dict[str, Any]:
        """Retrieve metadata for the local object."""
        path = self._resolve(key)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"No object found at storage key '{key}'.")
        stat = path.stat()
        content_type = "application/octet-stream"
        if key.endswith(".csv"):
            content_type = "text/csv"
        elif key.endswith(".json"):
            content_type = "application/json"
        elif key.endswith(".parquet"):
            content_type = "application/vnd.apache.parquet"

        return {
            "key": key,
            "size_bytes": stat.st_size,
            "content_type": content_type,
            "last_modified": stat.st_mtime,
        }
