"""
SQL safety validation.

This module is the enforcement point that makes it safe to execute
LLM-generated SQL. It does not trust the prompt to have produced safe SQL --
every rule here is checked in code, deterministically, against the actual
query text and the actual database schema. Nothing here calls the LLM.

Checks performed, in order:
  1. Reject empty / non-string input.
  2. Reject multiple statements (semicolon-separated, except one trailing ';').
  3. Reject anything that is not a single SELECT / WITH...SELECT statement.
  4. Reject a fixed blocklist of DDL/DML keywords, matched as whole tokens
     (so a column literally named "updates" is not blocked).
  5. Reject references to system/catalog tables (sqlite_master, pragma,
     information_schema, ...).
  6. Reject UNION-based statements (common injection / exfiltration vector
     for a single-table analytics tool).
  7. Validate that every table referenced actually exists in the schema,
     and (heuristically) that referenced columns exist somewhere in the
     schema -- catches hallucinated column names before they hit the DB.
  8. Inject a LIMIT clause if the query does not already have one.

`validate_sql` returns a ValidationResult; it never raises for "the SQL is
unsafe" -- it only raises for programmer errors (e.g. bad schema input).
Callers must check `.is_valid` before executing `.safe_sql`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.data.database import TableSchema

_FORBIDDEN_KEYWORDS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "truncate",
    "create",
    "replace",
    "grant",
    "revoke",
    "attach",
    "detach",
    "pragma",
    "vacuum",
    "exec",
    "execute",
    "call",
    "load_extension",
    "readfile",
    "writefile",
}

_SYSTEM_TABLE_PATTERNS = [
    re.compile(r"\bsqlite_master\b", re.IGNORECASE),
    re.compile(r"\bsqlite_temp_master\b", re.IGNORECASE),
    re.compile(r"\binformation_schema\b", re.IGNORECASE),
    re.compile(r"\bpg_catalog\b", re.IGNORECASE),
    re.compile(r"\bduckdb_\w+\b", re.IGNORECASE),
]

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class ValidationResult:
    is_valid: bool
    safe_sql: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _strip_trailing_semicolon(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


def _has_multiple_statements(sql: str) -> bool:
    body = _strip_trailing_semicolon(sql)
    # A ';' inside a quoted string literal is legal; only count top-level ones.
    depth_single = depth_double = False
    for ch in body:
        if ch == "'" and not depth_double:
            depth_single = not depth_single
        elif ch == '"' and not depth_single:
            depth_double = not depth_double
        elif ch == ";" and not depth_single and not depth_double:
            return True
    return False


def _extract_referenced_tables(sql: str) -> set[str]:
    tables = set()
    for match in re.finditer(r'\b(?:from|join)\s+"?([A-Za-z_][A-Za-z0-9_]*)"?', sql, re.IGNORECASE):
        tables.add(match.group(1).lower())
    return tables


def _extract_cte_names(sql: str) -> set[str]:
    """Extract names introduced by a `WITH name AS (...)` (and, for
    `WITH a AS (...), b AS (...)`, every comma-separated definition) so they
    can be treated as known "tables" for the rest of validation. This does
    NOT skip validating what's inside the CTE body -- `_extract_referenced_tables`
    scans the entire SQL text, including inside the parentheses, so a CTE
    that itself selects from a nonexistent table is still caught.

    This is a regex-based heuristic, not a full SQL parser (none was
    installable offline in this project's dev environment -- see README).
    It handles the common case (one or more top-level CTEs before the final
    SELECT) and will not correctly handle deeply nested or exotic CTE
    syntax; that tradeoff is documented rather than silently assumed."""
    if not re.match(r"\s*with\b", sql, re.IGNORECASE):
        return set()
    names = set()
    for match in re.finditer(r"(?:with|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(", sql, re.IGNORECASE):
        names.add(match.group(1).lower())
    return names


def validate_sql(sql: str, schema: dict[str, TableSchema], max_result_rows: int = 1000) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if not sql or not sql.strip():
        return ValidationResult(is_valid=False, errors=["SQL is empty."])

    if _has_multiple_statements(sql):
        return ValidationResult(is_valid=False, errors=["Multiple SQL statements are not allowed."])

    body = _strip_trailing_semicolon(sql)
    normalized = body.strip().lower()

    if not (normalized.startswith("select") or normalized.startswith("with")):
        return ValidationResult(
            is_valid=False,
            errors=["Only SELECT (or WITH ... SELECT) statements are allowed."],
        )

    tokens = {t.lower() for t in _TOKEN_RE.findall(body)}
    forbidden_hits = tokens & _FORBIDDEN_KEYWORDS
    if forbidden_hits:
        return ValidationResult(
            is_valid=False,
            errors=[f"Query contains forbidden keyword(s): {', '.join(sorted(forbidden_hits))}."],
        )

    for pattern in _SYSTEM_TABLE_PATTERNS:
        if pattern.search(body):
            return ValidationResult(
                is_valid=False, errors=["Query references a system/catalog table, which is not allowed."]
            )

    if re.search(r"\bunion\b", body, re.IGNORECASE):
        return ValidationResult(is_valid=False, errors=["UNION queries are not allowed."])

    referenced_tables = _extract_referenced_tables(body)
    cte_names = _extract_cte_names(body)
    known_tables = {name.lower() for name in schema.keys()}
    unknown_tables = referenced_tables - known_tables - cte_names
    if not referenced_tables:
        errors.append("Could not identify a table in the FROM clause.")
    elif unknown_tables:
        errors.append(
            f"Query references unknown table(s): {', '.join(sorted(unknown_tables))}. "
            f"Known tables: {', '.join(sorted(known_tables)) or '(none loaded)'}."
        )

    if referenced_tables and not unknown_tables:
        known_columns: set[str] = set()
        for table_name in referenced_tables:
            table = schema.get(table_name) or next((v for k, v in schema.items() if k.lower() == table_name), None)
            if table:
                known_columns |= {c[0].lower() for c in table.columns}
        select_clause_match = re.search(r"select\s+(.*?)\s+from\s", body, re.IGNORECASE | re.DOTALL)
        if select_clause_match:
            select_clause = select_clause_match.group(1)
            if select_clause.strip() != "*":
                candidate_cols = {
                    t.lower() for t in _TOKEN_RE.findall(select_clause) if t.lower() not in _SQL_FUNCTION_WORDS
                }
                unknown_cols = candidate_cols - known_columns - known_tables
                # Heuristic only (aliases and expressions can produce false
                # positives), so this is surfaced as a warning, not a hard error.
                if unknown_cols:
                    warnings.append(
                        f"Could not confirm these look like real columns (may be aliases/expressions): "
                        f"{', '.join(sorted(unknown_cols))}."
                    )

    if errors:
        return ValidationResult(is_valid=False, errors=errors, warnings=warnings)

    safe_sql = body
    if not re.search(r"\blimit\s+\d+\b", safe_sql, re.IGNORECASE):
        safe_sql = f"{safe_sql} LIMIT {max_result_rows}"
        warnings.append(f"No LIMIT clause found; automatically added LIMIT {max_result_rows}.")
    else:
        stated_limit = int(re.search(r"\blimit\s+(\d+)\b", safe_sql, re.IGNORECASE).group(1))
        if stated_limit > max_result_rows:
            safe_sql = re.sub(r"\blimit\s+\d+\b", f"LIMIT {max_result_rows}", safe_sql, flags=re.IGNORECASE)
            warnings.append(f"Requested LIMIT exceeded the maximum; capped at {max_result_rows}.")

    return ValidationResult(is_valid=True, safe_sql=safe_sql, errors=[], warnings=warnings)


_SQL_FUNCTION_WORDS = {
    "select",
    "as",
    "sum",
    "avg",
    "count",
    "min",
    "max",
    "distinct",
    "case",
    "when",
    "then",
    "else",
    "end",
    "and",
    "or",
    "not",
    "in",
    "is",
    "null",
    "like",
    "between",
    "order",
    "by",
    "group",
    "having",
    "asc",
    "desc",
    "cast",
    "round",
    "strftime",
    "date",
    "coalesce",
    "over",
    "partition",
    "row_number",
    "rank",
    "with",
    "on",
    "left",
    "right",
    "inner",
    "join",
    "from",
    "where",
    "limit",
    "julianday",
}
