"""
Tests for the dataset ingestion and processing pipeline.

Verifies end-to-end flow:
  - Storage persistence
  - Metadata extraction
  - Database updates
  - Status lifecycle transitions
  - Idempotency
  - Error isolation
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.repositories.dataset_repo import DatasetRepository
from app.db.repositories.project_repo import ProjectRepository
from app.db.session import session_scope
from app.services.ingestion import IngestionService
from app.storage.local_storage import LocalStorageBackend


@pytest.fixture
def ingestion_env():
    """Sets up an isolated in-memory DB and temporary local storage backend."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LocalStorageBackend(root=tmpdir)
        service = IngestionService(storage=storage, session_factory=factory)
        yield {"service": service, "storage": storage, "factory": factory}

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


class TestIngestionPipeline:
    def test_end_to_end_csv_ingestion(self, ingestion_env):
        service = ingestion_env["service"]
        storage = ingestion_env["storage"]
        factory = ingestion_env["factory"]

        # 1. Create a project
        with session_scope(factory) as session:
            p_repo = ProjectRepository(session)
            proj = p_repo.create_project(name="Pipeline Project")
            project_id = proj.id

        # 2. Ingest CSV data
        csv_data = b"order_id,product,revenue\n101,Widget,49.99\n102,Gadget,89.50\n103,Doohickey,12.00\n"
        dataset = service.ingest_file(
            project_id=project_id,
            filename="orders.csv",
            file_bytes=csv_data,
        )

        assert dataset.processing_status == "READY"
        assert dataset.row_count == 3
        assert dataset.column_count == 3
        assert dataset.file_type == "csv"
        assert dataset.checksum != ""
        dataset_id = dataset.id

        # 3. Verify raw file and metadata in storage
        assert storage.object_exists(dataset.s3_key) is True
        prof_key = f"metadata/{project_id}/{dataset_id}/profile.json"
        schema_key = f"metadata/{project_id}/{dataset_id}/semantic_schema.json"
        assert storage.object_exists(prof_key) is True
        assert storage.object_exists(schema_key) is True

        # 4. Verify profile and schema content
        profile_bytes = storage.get_object(prof_key)
        profile_dict = json.loads(profile_bytes.decode("utf-8"))
        assert profile_dict["row_count"] == 3
        assert profile_dict["column_count"] == 3

        schema_bytes = storage.get_object(schema_key)
        schema_dict = json.loads(schema_bytes.decode("utf-8"))
        assert len(schema_dict["columns"]) == 3
        rev_col = next(c for c in schema_dict["columns"] if c["name"] == "revenue")
        assert rev_col["semantic_type"] == "currency"

        # 5. Verify database columns
        with session_scope(factory) as session:
            d_repo = DatasetRepository(session)
            cols = d_repo.get_columns(dataset_id)
            assert len(cols) == 3
            col_names = {c.name for c in cols}
            assert col_names == {"order_id", "product", "revenue"}

    def test_idempotent_upload_does_not_reprocess(self, ingestion_env):
        service = ingestion_env["service"]
        factory = ingestion_env["factory"]

        with session_scope(factory) as session:
            p_repo = ProjectRepository(session)
            proj = p_repo.create_project(name="Idempotent Project")
            project_id = proj.id

        csv_data = b"id,val\n1,100\n2,200\n"

        # First upload
        ds1 = service.ingest_file(project_id=project_id, filename="data.csv", file_bytes=csv_data)
        assert ds1.processing_status == "READY"

        # Second upload with identical bytes and project
        ds2 = service.ingest_file(project_id=project_id, filename="data.csv", file_bytes=csv_data)
        assert ds2.id == ds1.id
        assert ds2.checksum == ds1.checksum

    def test_invalid_file_marks_status_failed(self, ingestion_env):
        service = ingestion_env["service"]
        factory = ingestion_env["factory"]

        with session_scope(factory) as session:
            p_repo = ProjectRepository(session)
            proj = p_repo.create_project(name="Failure Project")
            project_id = proj.id

        # Malformed bytes that cannot be parsed as CSV or XLSX
        bad_data = b"PK\x03\x04corrupted_binary_data_not_a_valid_zip_or_csv"
        dataset = service.ingest_file(
            project_id=project_id,
            filename="broken.xlsx",
            file_bytes=bad_data,
        )

        assert dataset.processing_status == "FAILED"
        assert dataset.processing_error is not None
        assert "error" in dataset.processing_error.lower() or "zip" in dataset.processing_error.lower() or "invalid" in dataset.processing_error.lower()
