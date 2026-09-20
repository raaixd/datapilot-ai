"""
Storage backend abstraction.

Defines the StorageBackend protocol that both local and cloud implementations
must satisfy. The application logic (pipeline, API) only ever speaks to this
interface — the infrastructure adapter (local filesystem vs S3) is injected
at startup by app/storage/factory.py based on LOCAL_MODE.

WHY A PROTOCOL RATHER THAN AN ABSTRACT BASE CLASS:
Python's typing.Protocol uses structural subtyping — any class with the
required methods is a valid implementation. This keeps concrete backends
independently testable without inheriting from a shared base, and it avoids
a circular import between this module and the concrete implementations.

KEY DESIGN DECISIONS:
- put_object returns the final key (not a URL) — the caller requests a
  presigned URL separately if needed.
- get_object returns raw bytes — deserialisation is the caller's concern.
- object_exists is idempotent and cheap — used for idempotency checks in
  the Lambda processor.
- generate_presigned_url is used for temporary frontend download links;
  the local adapter returns a file:// URL.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Protocol for cloud-agnostic object storage.

    All implementations must be thread-safe and idempotent for put_object
    (writing the same key twice must be safe and result in the last write
    winning, same as S3's default behaviour).
    """

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Store bytes at the given key. Returns the key (unchanged).

        Args:
            key:          Storage key, e.g. "raw/proj-id/ds-id/data.csv".
            data:         Raw bytes to store.
            content_type: MIME type hint (e.g. "text/csv", "application/json").

        Returns:
            The key that was written (for logging / correlation).
        """
        ...

    def get_object(self, key: str) -> bytes:
        """Retrieve bytes stored at the given key.

        Raises:
            FileNotFoundError: if the key does not exist.
        """
        ...

    def object_exists(self, key: str) -> bool:
        """Return True if an object exists at the given key."""
        ...

    def generate_presigned_url(self, key: str, expiry_seconds: int = 3600) -> str:
        """Return a URL that grants temporary access to the object.

        For S3StorageBackend: returns a signed S3 URL.
        For LocalStorageBackend: returns a file:// URI pointing to the
        local path (suitable for development only).

        Args:
            key:            Storage key of the object.
            expiry_seconds: How long the URL should remain valid (S3 only).

        Returns:
            A URL string.
        """
        ...

    def delete_object(self, key: str) -> None:
        """Delete the object at key if it exists. Idempotent — no error if
        the key does not exist."""
        ...


# ---------------------------------------------------------------------------
# Key structure helpers
# ---------------------------------------------------------------------------

def raw_key(project_id: str, dataset_id: str, filename: str) -> str:
    """S3 key for the raw (original) uploaded file."""
    return f"raw/{project_id}/{dataset_id}/{filename}"


def profile_key(project_id: str, dataset_id: str) -> str:
    """S3 key for the dataset profiling result JSON."""
    return f"metadata/{project_id}/{dataset_id}/profile.json"


def semantic_schema_key(project_id: str, dataset_id: str) -> str:
    """S3 key for the semantic schema JSON."""
    return f"metadata/{project_id}/{dataset_id}/semantic_schema.json"


def processed_key(project_id: str, dataset_id: str) -> str:
    """S3 key for the processed/normalised dataset."""
    return f"processed/{project_id}/{dataset_id}/normalized.parquet"


def report_key(project_id: str, analysis_id: str) -> str:
    """S3 key for a generated analytical report."""
    return f"reports/{project_id}/{analysis_id}/report.json"
