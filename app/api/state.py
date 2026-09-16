"""Dataset-scoped API state for the in-process demo service."""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

import pandas as pd

from app.agents.orchestrator import Orchestrator
from app.core.config import Settings
from app.data.database import AnalyticalDatabase, TableSchema
from app.data.profiler import DataProfile, DataProfiler
from app.llm.factory import build_llm_client


@dataclass
class DatasetState:
    db: AnalyticalDatabase
    orchestrator: Orchestrator
    profile: DataProfile
    schema: TableSchema


class DatasetRegistry:
    """Keep each caller-selected dataset independent within this process."""

    def __init__(self, settings: Settings, llm_factory=build_llm_client):
        self._settings = settings
        self._llm_factory = llm_factory
        self._states: dict[str, DatasetState] = {}
        self._lock = RLock()

    def load_dataframe(self, dataset: str, df: pd.DataFrame, table_name: str) -> DatasetState:
        db = AnalyticalDatabase(backend=self._settings.database_backend, path=":memory:")
        schema = db.load_dataframe(df, table_name)
        state = DatasetState(
            db=db,
            orchestrator=Orchestrator(db, self._llm_factory(self._settings), self._settings.max_result_rows, self._settings),
            profile=DataProfiler().profile(df, dataset_name=table_name),
            schema=schema,
        )
        key = _dataset_key(dataset)
        with self._lock:
            previous = self._states.get(key)
            self._states[key] = state
        if previous:
            previous.db.close()
        return state

    def get(self, dataset: str) -> DatasetState | None:
        with self._lock:
            return self._states.get(_dataset_key(dataset))

    def count(self) -> int:
        with self._lock:
            return len(self._states)


def _dataset_key(dataset: str) -> str:
    return (dataset or "default").strip() or "default"
