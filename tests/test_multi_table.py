"""
Tests for multi-table support, archive loading, and relationship detection.
"""

import io
import unittest
import zipfile

import pandas as pd

from app.agents.orchestrator import Orchestrator
from app.data.database import AnalyticalDatabase
from app.data.loader import load_tabular_archive
from app.llm.mock_client import MockLLMClient


class TestMultiTable(unittest.TestCase):
    def setUp(self):
        self.db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        self.customers_df = pd.DataFrame(
            {
                "customer_id": [1, 2, 3],
                "customer_name": ["Alice", "Bob", "Charlie"],
                "region": ["North", "South", "East"],
            }
        )
        self.orders_df = pd.DataFrame(
            {
                "order_id": [101, 102, 103, 104],
                "customer_id": [1, 2, 1, 3],
                "revenue": [150.0, 200.0, 50.0, 300.0],
                "quantity": [2, 1, 1, 4],
            }
        )

    def tearDown(self):
        self.db.close()

    def test_load_multiple_tables_coexist(self):
        self.db.load_dataframe(self.customers_df, "customers")
        self.db.load_dataframe(self.orders_df, "orders")

        schema = self.db.describe_schema()
        self.assertIn("customers", schema)
        self.assertIn("orders", schema)
        self.assertEqual(schema["customers"].row_count, 3)
        self.assertEqual(schema["orders"].row_count, 4)

    def test_drop_single_table_preserves_other(self):
        self.db.load_dataframe(self.customers_df, "customers")
        self.db.load_dataframe(self.orders_df, "orders")

        self.db.drop_table("customers")
        schema = self.db.describe_schema()
        self.assertNotIn("customers", schema)
        self.assertIn("orders", schema)

    def test_detect_relationships_finds_shared_key(self):
        self.db.load_dataframe(self.customers_df, "customers")
        self.db.load_dataframe(self.orders_df, "orders")

        rels = self.db.detect_relationships()
        self.assertGreater(len(rels), 0)
        # Should detect customer_id link between orders and customers
        has_cust_rel = any(
            (r["from_table"] == "customers" and r["to_table"] == "orders" and r["from_column"] == "customer_id")
            or (r["from_table"] == "orders" and r["to_table"] == "customers" and r["from_column"] == "customer_id")
            for r in rels
        )
        self.assertTrue(has_cust_rel, f"Relationships found: {rels}")

    def test_load_tabular_archive_extracts_all_tables(self):
        # Create an in-memory zip containing two CSV files
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as z:
            z.writestr("customers.csv", "customer_id,name\n1,Alice\n2,Bob\n")
            z.writestr("orders.csv", "order_id,amount\n101,50.0\n102,99.9\n")
            z.writestr("notes.txt", "This text file should be ignored\n")
        zip_buf.seek(0)

        tables = load_tabular_archive(zip_buf, "test_archive.zip")
        self.assertIn("customers", tables)
        self.assertIn("orders", tables)
        self.assertNotIn("notes", tables)
        self.assertEqual(len(tables["customers"]), 2)
        self.assertEqual(len(tables["orders"]), 2)

    def test_orchestrator_selects_correct_table_in_multi_table_schema(self):
        self.db.load_dataframe(self.customers_df, "customers")
        self.db.load_dataframe(self.orders_df, "orders")

        orchestrator = Orchestrator(self.db, MockLLMClient())
        # Ask question about orders table (revenue)
        res_orders = orchestrator.analyze("What is the total revenue?")
        self.assertTrue(res_orders.success)
        self.assertIn("orders", res_orders.sql.lower())
        self.assertEqual(res_orders.metrics.get("total_revenue"), 700.0)


if __name__ == "__main__":
    unittest.main()
