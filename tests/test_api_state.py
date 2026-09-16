import unittest

import pandas as pd

from app.api.state import DatasetRegistry
from app.core.config import Settings
from app.llm.mock_client import MockLLMClient


class TestDatasetRegistry(unittest.TestCase):
    def test_named_datasets_keep_independent_data_and_profiles(self):
        registry = DatasetRegistry(Settings(llm_provider="mock"), llm_factory=lambda _settings: MockLLMClient())
        registry.load_dataframe("north", pd.DataFrame({"revenue": [10]}), "sales")
        registry.load_dataframe("south", pd.DataFrame({"revenue": [20]}), "sales")

        north = registry.get("north").orchestrator.analyze("What is the total revenue?", registry.get("north").profile)
        south = registry.get("south").orchestrator.analyze("What is the total revenue?", registry.get("south").profile)

        self.assertEqual(north.metrics["total_revenue"], 10)
        self.assertEqual(south.metrics["total_revenue"], 20)
