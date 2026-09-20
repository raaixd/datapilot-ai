"""
Tests for Alembic migrations and database initialization.
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.db.session import init_db


class TestDatabaseMigration:
    def test_init_db_creates_all_tables(self):
        """Verify init_db() creates all metadata tables without error."""
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        try:
            init_db(engine)
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            expected_tables = {
                "projects",
                "datasets",
                "dataset_columns",
                "analysis_runs",
                "analysis_queries",
                "analysis_results",
                "insights",
                "reports",
                "evaluation_runs",
            }
            assert expected_tables.issubset(tables)
        finally:
            engine.dispose()

    def test_alembic_upgrade_to_head(self):
        """Verify Alembic can run upgrade cleanly against a target database."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()

        db_url = f"sqlite:///{tmp_path.as_posix()}"
        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)

        try:
            command.upgrade(alembic_cfg, "head")

            engine = create_engine(db_url)
            try:
                inspector = inspect(engine)
                tables = set(inspector.get_table_names())
                assert "alembic_version" in tables
                assert "projects" in tables
                assert "datasets" in tables
                assert "dataset_columns" in tables
                assert "analysis_runs" in tables
                assert "analysis_queries" in tables
                assert "analysis_results" in tables
                assert "insights" in tables
                assert "reports" in tables
                assert "evaluation_runs" in tables
            finally:
                engine.dispose()
        finally:
            gc.collect()
            try:
                tmp_path.unlink(missing_ok=True)
            except PermissionError:
                pass
