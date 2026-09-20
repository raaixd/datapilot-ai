"""
Dataset Ingestion and Processing Pipeline Service.

Implements the end-to-end cloud-native dataset ingestion workflow:
  1. USER UPLOAD
  2. Checksum calculation & idempotency validation
  3. Persist raw object to StorageBackend (S3 or local filesystem)
  4. Mark dataset UPLOADED in RDS / SQLite
  5. Validate tabular structure (CSV / XLSX) -> VALIDATING
  6. Deterministic profiling & semantic schema extraction -> PROCESSING
  7. Persist metadata artifacts (profile.json, semantic_schema.json) to StorageBackend
  8. Store typed column records in database
  9. Mark dataset READY (or FAILED with error trace)

Runs identically in LOCAL_MODE and in AWS environments.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import uuid

from app.core.config import get_settings
from app.data.loader import load_tabular_file
from app.data.profiler import DataProfiler
from app.data.semantic_schema import SemanticSchemaGenerator
from app.db.models import Dataset
from app.db.repositories.dataset_repo import DatasetRepository
from app.db.session import get_session_factory, session_scope
from app.storage.base import StorageBackend, profile_key, raw_key, semantic_schema_key
from app.storage.factory import build_storage_backend_from_settings

logger = logging.getLogger(__name__)


class IngestionService:
    """Orchestrates dataset storage, validation, profiling, and metadata persistence."""

    def __init__(
        self,
        storage: StorageBackend | None = None,
        profiler: DataProfiler | None = None,
        schema_generator: SemanticSchemaGenerator | None = None,
        session_factory=None,
    ):
        settings = get_settings()
        self._storage = storage or build_storage_backend_from_settings(settings)
        self._profiler = profiler or DataProfiler()
        self._schema_generator = schema_generator or SemanticSchemaGenerator(self._profiler)
        self._session_factory = session_factory or get_session_factory()

    def ingest_file(
        self,
        project_id: str,
        filename: str,
        file_bytes: bytes,
        dataset_id: str | None = None,
        auto_process: bool = True,
    ) -> Dataset:
        """Ingest a tabular file, verify idempotency, upload to storage, and process.

        Args:
            project_id:   The ID of the project owning this dataset.
            filename:     The original uploaded filename.
            file_bytes:   Raw bytes of the file.
            dataset_id:   Optional explicit ID (generated if omitted).
            auto_process: If True, immediately triggers the validation & profiling pipeline.

        Returns:
            The created or existing Dataset model instance.
        """
        checksum = hashlib.sha256(file_bytes).hexdigest()
        file_type = filename.rsplit(".", 1)[-1].lower() if "." in filename else "csv"
        ds_id = dataset_id or str(uuid.uuid4())

        # 1. Idempotency check: if dataset with matching checksum exists in project and is READY
        with session_scope(self._session_factory) as session:
            repo = DatasetRepository(session)
            existing = repo.get_by_checksum(project_id, checksum)
            if existing and existing.processing_status == "READY":
                logger.info(
                    '{"event": "dataset_upload_idempotent_hit", "project_id": "%s", "dataset_id": "%s", "checksum": "%s"}',
                    project_id,
                    existing.id,
                    checksum,
                )
                return existing

        # 2. Upload raw file to storage backend (S3 or local filesystem)
        storage_key = raw_key(project_id, ds_id, filename)
        content_type = "text/csv" if file_type == "csv" else "application/octet-stream"
        self._storage.put_object(storage_key, file_bytes, content_type=content_type)
        logger.info(
            '{"event": "dataset_raw_stored", "dataset_id": "%s", "key": "%s", "size_bytes": %d}',
            ds_id,
            storage_key,
            len(file_bytes),
        )

        # 3. Create or update database record in UPLOADED state
        with session_scope(self._session_factory) as session:
            repo = DatasetRepository(session)
            dataset = repo.get_dataset(ds_id)
            if not dataset:
                dataset = repo.create_dataset(
                    project_id=project_id,
                    filename=filename,
                    file_type=file_type,
                    s3_key=storage_key,
                    file_size=len(file_bytes),
                    checksum=checksum,
                    processing_status="UPLOADED",
                    dataset_id=ds_id,
                )
            else:
                repo.update_status(ds_id, "UPLOADED")

        # 4. Run processing pipeline if auto_process is True
        if auto_process:
            return self.process_dataset(project_id, ds_id)

        with session_scope(self._session_factory) as session:
            repo = DatasetRepository(session)
            return repo.get_dataset(ds_id)

    def process_dataset(self, project_id: str, dataset_id: str) -> Dataset:
        """Executes the validation, profiling, and metadata generation pipeline.

        Idempotent: can be safely re-run on any dataset.
        """
        logger.info('{"event": "dataset_processing_started", "dataset_id": "%s"}', dataset_id)

        # Retrieve dataset record
        with session_scope(self._session_factory) as session:
            repo = DatasetRepository(session)
            dataset = repo.get_dataset(dataset_id)
            if not dataset:
                raise ValueError(f"Dataset '{dataset_id}' not found.")
            storage_key = dataset.s3_key
            filename = dataset.filename
            repo.update_status(dataset_id, "VALIDATING")

        try:
            # 1. Download raw data from storage
            raw_bytes = self._storage.get_object(storage_key)

            # 2. Parse and validate DataFrame structure
            df = load_tabular_file(io.BytesIO(raw_bytes), filename)

            # 3. Mark state as PROCESSING
            with session_scope(self._session_factory) as session:
                repo = DatasetRepository(session)
                repo.update_status(dataset_id, "PROCESSING")

            # 4. Generate deterministic profile
            profile = self._profiler.profile(df, dataset_name=filename)

            # 5. Generate semantic schema
            semantic_schema = self._schema_generator.generate(df, dataset_id=dataset_id, profile=profile)

            # 6. Store metadata artifacts in storage backend
            prof_key = profile_key(project_id, dataset_id)
            schema_key = semantic_schema_key(project_id, dataset_id)

            self._storage.put_object(
                prof_key,
                json.dumps(profile.to_dict(), indent=2).encode("utf-8"),
                content_type="application/json",
            )
            self._storage.put_object(
                schema_key,
                json.dumps(semantic_schema.to_dict(), indent=2).encode("utf-8"),
                content_type="application/json",
            )

            # 7. Persist column profiles and mark dataset READY
            columns_data = [
                {
                    "name": col.name,
                    "physical_type": col.physical_type,
                    "semantic_type": col.semantic_type,
                    "nullable": col.nullable,
                    "description": col.description,
                    "statistics": col.statistics,
                }
                for col in semantic_schema.columns
            ]

            with session_scope(self._session_factory) as session:
                repo = DatasetRepository(session)
                repo.set_columns(dataset_id, columns_data)
                updated = repo.update_status(
                    dataset_id,
                    status="READY",
                    row_count=profile.row_count,
                    column_count=profile.column_count,
                )
                logger.info(
                    '{"event": "dataset_processing_completed", "dataset_id": "%s", "rows": %d, "cols": %d, "status": "READY"}',
                    dataset_id,
                    profile.row_count,
                    profile.column_count,
                )
                return updated

        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.error(
                '{"event": "dataset_processing_failed", "dataset_id": "%s", "error": "%s"}',
                dataset_id,
                error_msg,
            )
            with session_scope(self._session_factory) as session:
                repo = DatasetRepository(session)
                failed = repo.update_status(dataset_id, status="FAILED", error=error_msg)
                return failed
