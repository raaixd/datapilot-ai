"""
Tests for the storage abstraction layer.

Tests only cover LocalStorageBackend (no AWS required). The S3StorageBackend
is covered by separate integration tests that require AWS credentials.

All tests use a temp directory fixture (tmp_path) so they are hermetic —
no files are left on disk after the test run.
"""

from __future__ import annotations

import pytest

from app.storage.base import StorageBackend, profile_key, raw_key, report_key
from app.storage.factory import build_storage_backend
from app.storage.local_storage import LocalStorageBackend

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> LocalStorageBackend:
    return LocalStorageBackend(root=str(tmp_path / "storage"))


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestStorageProtocol:
    def test_local_backend_satisfies_protocol(self, store):
        assert isinstance(store, StorageBackend)


# ---------------------------------------------------------------------------
# put_object / get_object
# ---------------------------------------------------------------------------


class TestPutGet:
    def test_put_and_get_roundtrip(self, store):
        key = "raw/proj/ds/data.csv"
        data = b"col1,col2\n1,2\n3,4"
        returned_key = store.put_object(key, data, content_type="text/csv")
        assert returned_key == key
        result = store.get_object(key)
        assert result == data

    def test_put_creates_parent_directories(self, store):
        key = "metadata/proj/ds/profile.json"
        store.put_object(key, b'{"rows": 100}', content_type="application/json")
        assert store.object_exists(key)

    def test_put_is_idempotent_last_write_wins(self, store):
        key = "raw/proj/ds/file.csv"
        store.put_object(key, b"original")
        store.put_object(key, b"overwritten")
        assert store.get_object(key) == b"overwritten"

    def test_get_missing_key_raises_file_not_found(self, store):
        with pytest.raises(FileNotFoundError, match="storage key"):
            store.get_object("does/not/exist.json")

    def test_put_returns_key_not_url(self, store):
        key = "some/key.bin"
        returned = store.put_object(key, b"data")
        assert returned == key
        assert not returned.startswith("http") and not returned.startswith("file://")

    def test_large_binary_roundtrip(self, store):
        key = "raw/proj/ds/large.bin"
        data = bytes(range(256)) * 1000  # 256KB
        store.put_object(key, data)
        assert store.get_object(key) == data


# ---------------------------------------------------------------------------
# object_exists
# ---------------------------------------------------------------------------


class TestObjectExists:
    def test_missing_key_returns_false(self, store):
        assert not store.object_exists("no/such/key.txt")

    def test_existing_key_returns_true(self, store):
        store.put_object("a/b/c.txt", b"hello")
        assert store.object_exists("a/b/c.txt")

    def test_directory_not_counted_as_object(self, store, tmp_path):
        import os
        os.makedirs(tmp_path / "storage" / "dir")
        assert not store.object_exists("dir")


# ---------------------------------------------------------------------------
# delete_object
# ---------------------------------------------------------------------------


class TestDeleteObject:
    def test_delete_existing_object(self, store):
        store.put_object("x/y.json", b"{}")
        store.delete_object("x/y.json")
        assert not store.object_exists("x/y.json")

    def test_delete_missing_key_is_noop(self, store):
        # Should not raise
        store.delete_object("nonexistent/key.csv")

    def test_delete_does_not_remove_siblings(self, store):
        store.put_object("dir/a.csv", b"a")
        store.put_object("dir/b.csv", b"b")
        store.delete_object("dir/a.csv")
        assert store.object_exists("dir/b.csv")


# ---------------------------------------------------------------------------
# presigned_url
# ---------------------------------------------------------------------------


class TestPresignedUrl:
    def test_presigned_url_is_file_scheme(self, store):
        store.put_object("some/file.csv", b"data")
        url = store.generate_presigned_url("some/file.csv")
        assert url.startswith("file://")

    def test_presigned_url_for_missing_key_raises(self, store):
        with pytest.raises(FileNotFoundError):
            store.generate_presigned_url("missing/key.csv")


# ---------------------------------------------------------------------------
# Path traversal guard
# ---------------------------------------------------------------------------


class TestPathTraversalGuard:
    def test_traversal_in_key_rejected(self, store):
        with pytest.raises(ValueError, match="resolves outside root"):
            store.put_object("../../etc/passwd", b"evil")

    def test_traversal_in_get_rejected(self, store):
        with pytest.raises(ValueError, match="resolves outside root"):
            store.get_object("../../../secret")

    def test_traversal_in_exists_returns_false(self, store):
        assert not store.object_exists("../../etc/passwd")


# ---------------------------------------------------------------------------
# Key structure helpers
# ---------------------------------------------------------------------------


class TestKeyHelpers:
    def test_raw_key(self):
        assert raw_key("proj-1", "ds-1", "data.csv") == "raw/proj-1/ds-1/data.csv"

    def test_profile_key(self):
        assert profile_key("proj-1", "ds-1") == "metadata/proj-1/ds-1/profile.json"

    def test_report_key(self):
        assert report_key("proj-1", "analysis-1") == "reports/proj-1/analysis-1/report.json"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestStorageFactory:
    def test_local_mode_true_returns_local_backend(self, tmp_path):
        backend = build_storage_backend(local_mode=True, local_storage_root=str(tmp_path / "s3"))
        assert isinstance(backend, LocalStorageBackend)

    def test_local_backend_from_factory_is_functional(self, tmp_path):
        backend = build_storage_backend(local_mode=True, local_storage_root=str(tmp_path / "s3"))
        backend.put_object("test/key.txt", b"hello")
        assert backend.get_object("test/key.txt") == b"hello"

    def test_s3_mode_without_bucket_raises(self, tmp_path):
        """S3StorageBackend raises at construction without a bucket name."""
        from app.storage.s3_storage import S3StorageBackend
        with pytest.raises(ValueError, match="AWS_S3_BUCKET"):
            S3StorageBackend(bucket="")
