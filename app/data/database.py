"""
Analytical database layer.

Design choice vs. the reference project: instead of a fixed SQLAlchemy ORM
schema seeded with one synthetic dataset, DataPilot AI is CSV-first --
a user uploads an arbitrary business CSV and it becomes a table in an
in-process analytical database. This means the schema is not known in
advance, so schema introspection (`describe_schema`) is a first-class
operation used by both the SQL validator and the prompt builder.

Two backends are supported behind one small interface:
  - "sqlite" (default): Python's built-in sqlite3, zero extra dependencies.
    This is the backend exercised by the automated test suite.
  - "duckdb": used the same way if the `duckdb` package is installed --
    better suited to larger analytical CSVs. Selected via
    DATABASE_BACKEND=duckdb.

The connection is opened read-only for querying (see `AnalyticalDatabase.query`)
so that even a validated-but-unexpected statement cannot mutate data.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pandas as pd


class UnsupportedBackendError(Exception):
    pass


@dataclass
class TableSchema:
    name: str
    columns: list[tuple[str, str]]  # (column_name, sql_type)
    row_count: int
    column_samples: dict[str, list[str]] = None  # low-cardinality column -> sample distinct values

    def __post_init__(self):
        if self.column_samples is None:
            self.column_samples = {}


class AnalyticalDatabase:
    """A small, backend-agnostic wrapper around an in-process analytical DB."""

    def __init__(self, backend: str = "sqlite", path: str = ":memory:"):
        self.backend = backend
        self.path = path

        if backend == "sqlite":
            self._conn = sqlite3.connect(
                self.path,
                check_same_thread=False,
            )
        elif backend == "duckdb":
            try:
                import duckdb  # type: ignore
            except ImportError as exc:  # pragma: no cover - exercised only without duckdb installed
                raise UnsupportedBackendError(
                    "DATABASE_BACKEND=duckdb requires the optional `duckdb` package. "
                    "Install it with `pip install duckdb`, or set DATABASE_BACKEND=sqlite."
                ) from exc

            self._conn = duckdb.connect(path)
        else:
            raise UnsupportedBackendError(
                f"Unknown DATABASE_BACKEND '{backend}'. Use 'sqlite' or 'duckdb'."
            )

    def load_dataframe(self, df: pd.DataFrame, table_name: str) -> TableSchema:
        """Register a DataFrame as a table, replacing any existing table of the same name."""
        table_name = _safe_identifier(table_name)

        if self.backend == "sqlite":
            df.to_sql(
                table_name,
                self._conn,
                if_exists="replace",
                index=False,
            )
        else:  # duckdb
            self._conn.register("_incoming_df", df)
            self._conn.execute(
                f'CREATE OR REPLACE TABLE "{table_name}" AS SELECT * FROM _incoming_df'
            )
            self._conn.unregister("_incoming_df")

        return self.describe_table(table_name)

    def describe_table(self, table_name: str) -> TableSchema:
        table_name = _safe_identifier(table_name)

        if self.backend == "sqlite":
            cursor = self._conn.execute(
                f'PRAGMA table_info("{table_name}")'
            )
            columns = [(row[1], row[2]) for row in cursor.fetchall()]
            row_count = self._conn.execute(
                f'SELECT COUNT(*) FROM "{table_name}"'
            ).fetchone()[0]
        else:
            info = self._conn.execute(
                f'DESCRIBE "{table_name}"'
            ).fetchall()
            columns = [(row[0], row[1]) for row in info]
            row_count = self._conn.execute(
                f'SELECT COUNT(*) FROM "{table_name}"'
            ).fetchone()[0]

        column_samples = self._sample_low_cardinality_columns(
            table_name,
            columns,
        )

        return TableSchema(
            name=table_name,
            columns=columns,
            row_count=row_count,
            column_samples=column_samples,
        )

    def _sample_low_cardinality_columns(
        self,
        table_name: str,
        columns: list[tuple[str, str]],
        max_distinct: int = 30,
        sample_size: int = 8,
    ) -> dict[str, list[str]]:
        """Fetch a handful of real distinct values for text-like, low-cardinality
        columns. This is what lets a filter like "...for the North region" be
        matched against an actual value that exists in the data, instead of the
        planner guessing at filter values it has never seen.
        """
        samples: dict[str, list[str]] = {}
        text_types = ("CHAR", "TEXT", "VARCHAR", "STRING", "OBJECT")

        for name, sqltype in columns:
            if not any(t in sqltype.upper() for t in text_types):
                continue

            try:
                distinct_count = self._conn.execute(
                    f'SELECT COUNT(DISTINCT "{name}") FROM "{table_name}"'
                ).fetchone()[0]

                if distinct_count == 0 or distinct_count > max_distinct:
                    continue

                rows = self._conn.execute(
                    f'SELECT DISTINCT "{name}" FROM "{table_name}" '
                    f'WHERE "{name}" IS NOT NULL LIMIT {sample_size}'
                ).fetchall()

                samples[name] = [str(r[0]) for r in rows]
            except Exception:
                continue

        return samples

    def list_tables(self) -> list[str]:
        if self.backend == "sqlite":
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        else:
            rows = self._conn.execute("SHOW TABLES").fetchall()

        return [r[0] for r in rows]

    def describe_schema(self) -> dict[str, TableSchema]:
        return {
            name: self.describe_table(name)
            for name in self.list_tables()
        }

    def query(self, sql: str, max_rows: int = 1000) -> pd.DataFrame:
        """Execute an already-validated, read-only SQL statement and return a DataFrame."""
        if self.backend == "sqlite":
            df = pd.read_sql_query(sql, self._conn)
        else:
            df = self._conn.execute(sql).fetch_df()

        if len(df) > max_rows:
            df = df.head(max_rows)

        return df

    def close(self) -> None:
        self._conn.close()


def _safe_identifier(name: str) -> str:
    """Reduce a free-form name (e.g. an uploaded filename) to a safe SQL identifier."""
    cleaned = "".join(
        ch if ch.isalnum() else "_"
        for ch in name
    )

    if not cleaned or cleaned[0].isdigit():
        cleaned = f"t_{cleaned}"

    return cleaned.lower()