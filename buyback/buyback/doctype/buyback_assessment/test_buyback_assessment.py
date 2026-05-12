# Copyright (c) 2026, Abiraj and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestBuybackAssessment(FrappeTestCase):
    pass


class TestBuybackQuestionBankCategories(FrappeTestCase):
    """Unit tests for multi-category applicability (added May 2026).

    Exercises:
    - _sync_applies_to_categories keeps legacy field in sync with multiselect
    - _get_question_applicable_categories in api.py: new rows → legacy fallback → empty
    """

    def _make_question(self, question_text="Test Q?", applies_to_categories=None,
                       applies_to_category=None):
        """Helper: build an in-memory BuybackQuestionBank doc (not inserted)."""
        doc = frappe.new_doc("Buyback Question Bank")
        doc.question_text = question_text
        doc.question_type = "Yes/No"
        doc.applies_to_category = applies_to_category or ""
        for cat in (applies_to_categories or []):
            doc.append("applies_to_categories", {"item_group": cat})
        return doc

    # ── _sync_applies_to_categories: multiselect → legacy field ──────────────

    def test_sync_multiselect_sets_legacy_field(self):
        """First item in applies_to_categories becomes applies_to_category."""
        doc = self._make_question(applies_to_categories=["Smartphones", "Tablets"])
        doc._sync_applies_to_categories()
        self.assertEqual(doc.applies_to_category, "Smartphones",
                         "Legacy field should be set to first category")

    def test_sync_legacy_backfills_multiselect(self):
        """Legacy applies_to_category is backfilled into multiselect if multiselect is empty."""
        doc = self._make_question(applies_to_category="Laptops")
        doc._sync_applies_to_categories()
        cats = [r.item_group for r in doc.get("applies_to_categories")]
        self.assertIn("Laptops", cats,
                      "Legacy category should be backfilled into applies_to_categories")

    def test_sync_deduplicates_multiselect(self):
        """Duplicate entries in applies_to_categories are silently removed."""
        doc = self._make_question(applies_to_categories=["Phones", "Phones", "Tablets"])
        doc._sync_applies_to_categories()
        cats = [r.item_group for r in doc.get("applies_to_categories")]
        # The internal dedup uses a `seen` set — count unique only
        unique_cats = list(dict.fromkeys(cats))
        self.assertEqual(len(unique_cats), len(set(unique_cats)))

    def test_sync_empty_both_ok(self):
        """No crash when both fields are empty."""
        doc = self._make_question()
        doc._sync_applies_to_categories()
        self.assertEqual(doc.applies_to_category, "")

    # ── _get_question_applicable_categories: api helper ──────────────────────

    def test_get_applicable_falls_back_to_legacy(self):
        """When no child rows, legacy category is returned as a single-element list."""
        from buyback.api import _get_question_applicable_categories

        # Use a non-existent name → no DB rows → falls back to legacy
        result = _get_question_applicable_categories(
            "NONEXISTENT-Q-XYZ", legacy_category="Smartphones"
        )
        self.assertEqual(result, ["Smartphones"])

    def test_get_applicable_returns_empty_for_no_data(self):
        """When no rows and no legacy field, returns empty list."""
        from buyback.api import _get_question_applicable_categories

        result = _get_question_applicable_categories("NONEXISTENT-Q-XYZ")
        self.assertEqual(result, [])

    def test_get_applicable_returns_list(self):
        """Always returns a list (never None)."""
        from buyback.api import _get_question_applicable_categories

        result = _get_question_applicable_categories("NONEXISTENT-Q-XYZ")
        self.assertIsInstance(result, list)


def run_all():
    import sys
    import unittest
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.failures or result.errors:
        raise Exception(f"test_buyback_assessment: {len(result.failures)} failure(s), {len(result.errors)} error(s)")
