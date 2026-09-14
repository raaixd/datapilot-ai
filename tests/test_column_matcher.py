import unittest

from app.data.column_matcher import (
    column_matches_concept,
    extract_limit,
    find_columns_for_concept,
    is_singular_ranking_phrase,
    mentioned_in_text,
    normalize,
)


class TestNormalize(unittest.TestCase):
    def test_underscore_and_case(self):
        self.assertEqual(normalize("Product_Name"), "product name")

    def test_mentioned_in_text_handles_underscore_vs_space(self):
        self.assertTrue(mentioned_in_text("product_category", "group by product category"))
        self.assertTrue(mentioned_in_text("product_category", "group by product_category"))
        self.assertFalse(mentioned_in_text("product_category", "group by region"))


class TestConceptMatching(unittest.TestCase):
    def test_revenue_synonyms(self):
        for col in ["revenue", "sales_amount", "total_sales", "amount"]:
            self.assertTrue(column_matches_concept(col, "revenue"), col)

    def test_product_synonyms(self):
        for col in ["product_name", "item_name", "item", "sku"]:
            self.assertTrue(column_matches_concept(col, "product"), col)

    def test_date_synonyms(self):
        for col in ["order_date", "transaction_date", "purchase_date"]:
            self.assertTrue(column_matches_concept(col, "date"), col)

    def test_unrelated_column_does_not_match(self):
        self.assertFalse(column_matches_concept("customer_email", "revenue"))

    def test_find_columns_for_concept_respects_type_filter(self):
        columns = [("sales_amount", "REAL"), ("item_name", "TEXT")]
        numeric_only = find_columns_for_concept(columns, "revenue", lambda t: "REAL" in t)
        self.assertEqual(numeric_only, ["sales_amount"])


class TestLimitExtraction(unittest.TestCase):
    def test_top_n_digit(self):
        self.assertEqual(extract_limit("show the top 5 products"), 5)

    def test_top_n_word(self):
        self.assertEqual(extract_limit("show the top five products"), 5)

    def test_first_n(self):
        self.assertEqual(extract_limit("first 10 rows"), 10)

    def test_the_n_best(self):
        self.assertEqual(extract_limit("the 3 best regions"), 3)

    def test_no_limit_present(self):
        self.assertIsNone(extract_limit("what is the total revenue"))


class TestSingularRankingPhrase(unittest.TestCase):
    def test_which_product_has(self):
        self.assertTrue(is_singular_ranking_phrase("which product has the highest revenue"))

    def test_what_product_sold(self):
        self.assertTrue(is_singular_ranking_phrase("what product sold the most units"))

    def test_plural_ranking_not_singular(self):
        self.assertFalse(is_singular_ranking_phrase("what are the top products by revenue"))


if __name__ == "__main__":
    unittest.main()
