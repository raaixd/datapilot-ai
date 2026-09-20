"""
Tests for semantic schema extraction and type inference.

Ensures deterministic classification without hallucinations, proper handling
of currency, identifiers, percentages, dates, categoricals, and unknown types.
"""

from __future__ import annotations

import pandas as pd

from app.data.semantic_schema import SemanticSchemaGenerator


class TestSemanticSchemaGenerator:
    def setup_method(self):
        self.generator = SemanticSchemaGenerator()

    def test_currency_inference(self):
        df = pd.DataFrame({
            "revenue": [100.5, 200.0, 350.25],
            "unit_price": [10.0, 15.0, 25.0],
            "total_sales": [1005.0, 3000.0, 8756.25],
        })
        schema = self.generator.generate(df, dataset_id="currency_test")
        col_map = {c.name: c for c in schema.columns}

        assert col_map["revenue"].semantic_type == "currency"
        assert col_map["unit_price"].semantic_type == "currency"
        assert col_map["total_sales"].semantic_type == "currency"

    def test_identifier_inference(self):
        df = pd.DataFrame({
            "order_id": ["ORD-101", "ORD-102", "ORD-103"],
            "customer_key": [1001, 1002, 1003],
            "sku": ["SKU-A", "SKU-B", "SKU-C"],
        })
        schema = self.generator.generate(df, dataset_id="id_test")
        col_map = {c.name: c for c in schema.columns}

        assert col_map["order_id"].semantic_type == "identifier"
        assert col_map["order_id"].is_identifier is True
        assert col_map["customer_key"].semantic_type == "identifier"
        assert col_map["sku"].semantic_type == "identifier"
        assert schema.primary_key_candidate == "order_id"

    def test_percentage_inference(self):
        df = pd.DataFrame({
            "discount_pct": [0.05, 0.10, 0.15],
            "tax_rate": [0.08, 0.08, 0.08],
            "profit_margin": [25.5, 30.0, 18.2],
        })
        schema = self.generator.generate(df, dataset_id="pct_test")
        col_map = {c.name: c for c in schema.columns}

        assert col_map["discount_pct"].semantic_type == "percentage"
        assert col_map["tax_rate"].semantic_type == "percentage"
        assert col_map["profit_margin"].semantic_type == "percentage"

    def test_datetime_inference(self):
        df = pd.DataFrame({
            "order_date": ["2024-01-15", "2024-02-20", "2024-03-25"],
            "timestamp": pd.date_range("2024-01-01", periods=3, freq="D"),
        })
        schema = self.generator.generate(df, dataset_id="dt_test")
        col_map = {c.name: c for c in schema.columns}

        assert col_map["order_date"].semantic_type == "datetime"
        assert col_map["timestamp"].semantic_type == "datetime"

    def test_boolean_inference(self):
        df = pd.DataFrame({
            "is_active": [True, False, True],
            "flag": [1, 0, 1],
        })
        schema = self.generator.generate(df, dataset_id="bool_test")
        col_map = {c.name: c for c in schema.columns}

        assert col_map["is_active"].semantic_type == "boolean"
        assert col_map["flag"].semantic_type == "boolean"

    def test_uncertain_data_falls_back_to_unknown_without_hallucinating(self):
        df = pd.DataFrame({
            "xyz_metric": ["foo", "bar", "baz", "qux"],
            "raw_blob_data": ["val1", "val2", "val3", "val4"],
        })
        schema = self.generator.generate(df, dataset_id="uncertain_test")
        col_map = {c.name: c for c in schema.columns}

        assert col_map["xyz_metric"].semantic_type in ("categorical", "unknown")
        assert col_map["raw_blob_data"].semantic_type in ("categorical", "unknown")
        # Ensure it does not pretend to be currency, percentage, or date
        assert col_map["xyz_metric"].semantic_type not in ("currency", "percentage", "datetime")

    def test_suspicious_columns_flagged(self):
        df = pd.DataFrame({
            "empty_col": [None, None, None],
            "constant_col": ["constant_val", "constant_val", "constant_val"],
            "normal_col": [1, 2, 3],
        })
        schema = self.generator.generate(df, dataset_id="suspicious_test")
        col_map = {c.name: c for c in schema.columns}

        assert col_map["empty_col"].is_suspicious is True
        assert col_map["constant_col"].is_suspicious is True
        assert col_map["normal_col"].is_suspicious is False
        assert "empty_col" in schema.suspicious_columns
        assert "constant_col" in schema.suspicious_columns

    def test_compact_context_rendering(self):
        df = pd.DataFrame({
            "order_id": [1, 2, 3],
            "revenue": [10.0, 20.0, 30.0],
            "region": ["North", "South", "East"],
        })
        schema = self.generator.generate(df, dataset_id="context_test")
        context = schema.to_compact_context()

        assert "Table schema (3 rows, 3 columns):" in context
        assert "order_id (identifier" in context
        assert "revenue (currency" in context
        assert "region (categorical" in context
