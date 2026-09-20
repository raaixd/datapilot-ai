"""
Repository for Dataset and DatasetColumn entities.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select

from app.db.models import Dataset, DatasetColumn
from app.db.repositories.base import BaseRepository


class DatasetRepository(BaseRepository):
    """Data access operations for datasets and their column profiles."""

    def create_dataset(
        self,
        project_id: str,
        filename: str,
        file_type: str,
        s3_key: str,
        file_size: int = 0,
        checksum: str = "",
        processing_status: str = "UPLOADED",
        dataset_id: str | None = None,
    ) -> Dataset:
        kwargs: dict[str, Any] = {
            "project_id": project_id,
            "filename": filename,
            "file_type": file_type.lower(),
            "s3_key": s3_key,
            "file_size": file_size,
            "checksum": checksum,
            "processing_status": processing_status,
        }
        if dataset_id:
            kwargs["id"] = dataset_id

        dataset = Dataset(**kwargs)
        self.session.add(dataset)
        self.session.flush()
        return dataset

    def get_dataset(self, dataset_id: str) -> Dataset | None:
        stmt = select(Dataset).where(Dataset.id == dataset_id)
        return self.session.scalars(stmt).first()

    def get_by_checksum(self, project_id: str, checksum: str) -> Dataset | None:
        """Find an existing dataset by checksum within a project for idempotency."""
        stmt = select(Dataset).where(Dataset.project_id == project_id, Dataset.checksum == checksum)
        return self.session.scalars(stmt).first()

    def list_datasets(self, project_id: str | None = None, limit: int = 100, offset: int = 0) -> list[Dataset]:
        stmt = select(Dataset)
        if project_id:
            stmt = stmt.where(Dataset.project_id == project_id)
        stmt = stmt.order_by(Dataset.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

    def update_status(
        self,
        dataset_id: str,
        status: str,
        error: str | None = None,
        row_count: int | None = None,
        column_count: int | None = None,
    ) -> Dataset | None:
        dataset = self.get_dataset(dataset_id)
        if not dataset:
            return None
        dataset.processing_status = status
        dataset.processing_error = error
        if row_count is not None:
            dataset.row_count = row_count
        if column_count is not None:
            dataset.column_count = column_count
        self.session.flush()
        return dataset

    def set_columns(self, dataset_id: str, columns_data: list[dict[str, Any]]) -> list[DatasetColumn]:
        """Replace all column metadata for the dataset with the provided definitions."""
        # Delete existing columns
        stmt = delete(DatasetColumn).where(DatasetColumn.dataset_id == dataset_id)
        self.session.execute(stmt)

        created_cols = []
        for col in columns_data:
            c = DatasetColumn(
                dataset_id=dataset_id,
                name=col["name"],
                physical_type=col.get("physical_type", "object"),
                semantic_type=col.get("semantic_type", "unknown"),
                nullable=col.get("nullable", True),
                description=col.get("description"),
                statistics_json=col.get("statistics") or col.get("statistics_json"),
            )
            self.session.add(c)
            created_cols.append(c)

        self.session.flush()
        return created_cols

    def get_columns(self, dataset_id: str) -> list[DatasetColumn]:
        stmt = select(DatasetColumn).where(DatasetColumn.dataset_id == dataset_id)
        return list(self.session.scalars(stmt).all())

    def delete_dataset(self, dataset_id: str) -> bool:
        dataset = self.get_dataset(dataset_id)
        if not dataset:
            return False
        self.session.delete(dataset)
        self.session.flush()
        return True
