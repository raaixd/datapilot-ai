"""
Semantic schema extraction and metadata generation.

Translates physical DataFrame types and DataProfile statistics into a
machine-readable semantic representation suitable for LLM reasoning:
  - Identifies physical types (int64, float64, object, datetime64)
  - Infers semantic types (currency, percentage, identifier, categorical, datetime, numeric, text, boolean, unknown)
  - Discovers primary key candidates, potential target variables, and suspicious columns
  - Does NOT hallucinate semantic meanings -- falls back to "unknown" whenever uncertain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.data.profiler import ColumnProfile, DataProfile, DataProfiler

_CURRENCY_KEYWORDS = {
    "revenue",
    "price",
    "sales",
    "cost",
    "amount",
    "profit",
    "fee",
    "salary",
    "budget",
    "expense",
    "charge",
    "balance",
    "payment",
    "fare",
    "tip",
    "spend",
}

_PERCENTAGE_KEYWORDS = {
    "pct",
    "percent",
    "percentage",
    "rate",
    "ratio",
    "margin",
    "discount",
    "share",
    "yield",
}

_ID_KEYWORDS = {
    "id",
    "key",
    "code",
    "num",
    "number",
    "sku",
    "uuid",
    "guid",
}

_TARGET_KEYWORDS = {
    "churn",
    "target",
    "label",
    "status",
    "fraud",
    "converted",
    "outcome",
    "default",
    "y",
}


def _tokenize(name: str) -> set[str]:
    """Tokenize a column name by snake_case, camelCase, and punctuation."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
    return set(re.findall(r"[a-z0-9]+", s2))


@dataclass
class SemanticColumn:
    name: str
    physical_type: str
    semantic_type: str  # currency | percentage | identifier | categorical | datetime | numeric | text | boolean | unknown
    nullable: bool
    description: str
    statistics: dict[str, Any] = field(default_factory=dict)
    is_identifier: bool = False
    is_potential_target: bool = False
    is_suspicious: bool = False
    suspicious_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "physical_type": self.physical_type,
            "semantic_type": self.semantic_type,
            "nullable": self.nullable,
            "description": self.description,
            "is_identifier": self.is_identifier,
            "is_potential_target": self.is_potential_target,
            "is_suspicious": self.is_suspicious,
            "suspicious_reason": self.suspicious_reason,
            "statistics": self.statistics,
        }


@dataclass
class SemanticSchema:
    dataset_id: str
    row_count: int
    column_count: int
    columns: list[SemanticColumn]
    primary_key_candidate: str | None = None
    target_variable_candidates: list[str] = field(default_factory=list)
    suspicious_columns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "primary_key_candidate": self.primary_key_candidate,
            "target_variable_candidates": self.target_variable_candidates,
            "suspicious_columns": self.suspicious_columns,
            "columns": [c.to_dict() for c in self.columns],
        }

    def to_compact_context(self) -> str:
        """Renders a compact, high-signal schema summary for LLM prompt injection."""
        lines = [f"Table schema ({self.row_count} rows, {self.column_count} columns):"]
        for c in self.columns:
            flags = []
            if c.is_identifier:
                flags.append("identifier")
            if c.is_potential_target:
                flags.append("target")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            lines.append(f"  - {c.name} ({c.semantic_type}, {c.physical_type}){flag_str}: {c.description}")
        return "\n".join(lines)


class SemanticSchemaGenerator:
    """Generates machine-readable semantic schemas from DataFrames and DataProfiles."""

    def __init__(self, profiler: DataProfiler | None = None):
        self._profiler = profiler or DataProfiler()

    def generate(
        self,
        df: pd.DataFrame,
        dataset_id: str = "dataset",
        profile: DataProfile | None = None,
    ) -> SemanticSchema:
        if profile is None:
            profile = self._profiler.profile(df, dataset_name=dataset_id)

        col_profiles: dict[str, ColumnProfile] = {c.name: c for c in profile.columns}

        semantic_columns: list[SemanticColumn] = []
        pk_candidates: list[str] = []
        target_candidates: list[str] = []
        suspicious_cols: list[str] = []

        total_rows = profile.row_count

        for col in df.columns:
            series = df[col]
            cp = col_profiles.get(str(col))
            name = str(col)
            tokens = _tokenize(name)
            physical_type = str(series.dtype)
            nullable = bool(series.isna().any())

            non_null_count = cp.non_null_count if cp else int(series.notna().sum())
            distinct_count = cp.distinct_count if cp else int(series.nunique(dropna=True))

            # Statistics dictionary
            stats: dict[str, Any] = {
                "distinct_count": distinct_count,
                "null_count": cp.null_count if cp else int(series.isna().sum()),
                "null_pct": cp.null_pct if cp else round((series.isna().sum() / max(total_rows, 1)) * 100, 2),
            }
            if cp and cp.min is not None:
                stats.update(
                    {
                        "min": cp.min,
                        "max": cp.max,
                        "mean": cp.mean,
                        "median": cp.median,
                        "std": cp.std,
                        "q25": cp.q25,
                        "q75": cp.q75,
                        "zero_pct": cp.zero_pct,
                    }
                )
            if cp and cp.top_values:
                stats["top_values"] = cp.top_values[:5]

            description_parts: list[str] = []
            is_identifier = False
            is_potential_target = False
            is_suspicious = False
            suspicious_reason = None

            # 1. Suspicious checks
            if non_null_count == 0:
                is_suspicious = True
                suspicious_reason = "Column contains only null values"
                description_parts.append("Empty column (100% nulls)")
            elif distinct_count == 1 and total_rows > 1:
                is_suspicious = True
                suspicious_reason = "Constant column with only a single distinct value"
                val = series.dropna().iloc[0] if len(series.dropna()) > 0 else "None"
                description_parts.append(f"Constant value: {val}")

            # 2. Determine semantic type deterministically
            if non_null_count == 0:
                semantic_type = "unknown"

            # Identifier check
            elif bool(tokens.intersection(_ID_KEYWORDS)) or name.lower().endswith(("_id", "_key", "_code", "_num")):
                is_identifier = True
                semantic_type = "identifier"
                description_parts.append("Identifier / code key")
                if distinct_count == total_rows and total_rows > 0:
                    pk_candidates.append(name)
                    description_parts.append("Unique key (100% distinct)")

            # Percentage / Rate check (takes priority over currency if both match, e.g. profit_margin)
            elif bool(tokens.intersection(_PERCENTAGE_KEYWORDS)) and pd.api.types.is_numeric_dtype(series):
                semantic_type = "percentage"
                description_parts.append("Rate, percentage, or ratio")

            # Currency / Monetary check
            elif bool(tokens.intersection(_CURRENCY_KEYWORDS)) and pd.api.types.is_numeric_dtype(series):
                semantic_type = "currency"
                description_parts.append("Monetary value / currency")

            # Datetime check
            elif cp and cp.inferred_type == "datetime":
                semantic_type = "datetime"
                description_parts.append("Date / timestamp")
                if cp.min_date and cp.max_date:
                    description_parts.append(f"Range: {cp.min_date[:10]} to {cp.max_date[:10]}")

            # Boolean check
            elif (
                pd.api.types.is_bool_dtype(series)
                or (cp and cp.inferred_type == "boolean")
                or (distinct_count == 2 and set(series.dropna().unique()).issubset({0, 1, "0", "1", "true", "false", "True", "False"}))
            ):
                semantic_type = "boolean"
                description_parts.append("Binary boolean flag")

            # Categorical check
            elif cp and cp.inferred_type == "categorical":
                semantic_type = "categorical"
                description_parts.append(f"Categorical with {distinct_count} distinct value(s)")

            # Numeric check
            elif pd.api.types.is_numeric_dtype(series):
                semantic_type = "numeric"
                description_parts.append("Numeric metric / measure")

            # Free-text check
            elif cp and cp.inferred_type == "text":
                if cp.avg_text_length and cp.avg_text_length > 20:
                    semantic_type = "text"
                    description_parts.append("Free-form text / description")
                else:
                    semantic_type = "unknown"
                    description_parts.append("Unstructured string data")

            # Fallback
            else:
                semantic_type = "unknown"
                description_parts.append("General data (uncertain semantic type)")

            # Check for potential target variable
            if bool(tokens.intersection(_TARGET_KEYWORDS)):
                is_potential_target = True
                target_candidates.append(name)

            if is_suspicious:
                suspicious_cols.append(name)

            description = "; ".join(description_parts)

            semantic_columns.append(
                SemanticColumn(
                    name=name,
                    physical_type=physical_type,
                    semantic_type=semantic_type,
                    nullable=nullable,
                    description=description,
                    statistics=stats,
                    is_identifier=is_identifier,
                    is_potential_target=is_potential_target,
                    is_suspicious=is_suspicious,
                    suspicious_reason=suspicious_reason,
                )
            )

        # Select primary key candidate if exactly one candidate or highest distinctness
        primary_key = pk_candidates[0] if pk_candidates else None

        return SemanticSchema(
            dataset_id=dataset_id,
            row_count=total_rows,
            column_count=len(df.columns),
            columns=semantic_columns,
            primary_key_candidate=primary_key,
            target_variable_candidates=target_candidates,
            suspicious_columns=suspicious_cols,
        )


def extract_semantic_schema(
    df: pd.DataFrame,
    profile: DataProfile | None = None,
    dataset_id: str = "dataset",
) -> SemanticSchema:
    """Convenience helper to generate a SemanticSchema."""
    return SemanticSchemaGenerator().generate(df=df, dataset_id=dataset_id, profile=profile)
