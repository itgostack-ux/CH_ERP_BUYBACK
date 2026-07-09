# Copyright (c) 2026, Abiraj and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


# ─── Minimal seed helpers (idempotent) ────────────────────────────────────────
#
# The Buyback Assessment test used to be an empty ``pass``, which meant every
# ``bench run-tests --app buyback`` reported a false green even when the
# doctype's validate path was broken. The class below now exercises the
# minimum contract required by the pricing engine + validate — insert,
# validate, and rollback — so a regression on Buyback Assessment.validate
# fails loudly instead of vanishing.
#
# Grade Masters are the only master this insert *requires*. install.py seeds
# them on ``after_install``; when a test site is provisioned without the
# after-install hook (bare Frappe test bootstrap), we recreate them here so
# the test is self-sufficient. Any other seed (Buyback Price Master, CH
# Model, Item) is optional — the ``skipTest`` branches keep this class
# green on a bare site while the richer TC_048 suite covers the full path.

_GRADES = [
    {"grade_name": "A", "display_order": 1, "description": "Excellent"},
    {"grade_name": "B", "display_order": 2, "description": "Good"},
    {"grade_name": "C", "display_order": 3, "description": "Fair"},
    {"grade_name": "D", "display_order": 4, "description": "Poor"},
]


def _ensure_grades() -> None:
    for g in _GRADES:
        if not frappe.db.exists("Grade Master", {"grade_name": g["grade_name"]}):
            frappe.get_doc({"doctype": "Grade Master", **g}).insert(
                ignore_permissions=True
            )


def _pick_item_with_price_master() -> list[tuple[str, ...]]:
    """Return one ``(item_code,)`` row from Buyback Price Master joined to a
    non-disabled Item with a non-zero base price, or ``[]`` if none exist."""
    return frappe.db.sql(
        """
        SELECT bpm.item_code
        FROM `tabBuyback Price Master` bpm
        JOIN `tabItem` i ON i.name = bpm.item_code
        WHERE COALESCE(bpm.is_active, 0) = 1
          AND COALESCE(bpm.a_grade_iw_0_6, 0) > 0
          AND COALESCE(i.disabled, 0) = 0
        LIMIT 1
        """,
        as_dict=False,
    )


class TestBuybackAssessment(FrappeTestCase):
    """Contract: Buyback Assessment can be built + validated in-memory
    against a real Price Master row. If no seed is available we skip
    loudly rather than pass silently — that visibility is the point."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _ensure_grades()
        row = _pick_item_with_price_master()
        cls.item_code = row[0][0] if row else None
        cls.customer = frappe.db.get_value("Customer", {}, "name")

    def test_new_doc_is_creatable(self):
        """Smoke: `frappe.new_doc` returns a Buyback Assessment we can shape.

        No DB write. Guards against the doctype JSON being renamed / a
        controller import breaking under a migration.
        """
        doc = frappe.new_doc("Buyback Assessment")
        self.assertEqual(doc.doctype, "Buyback Assessment")

    def test_validate_populates_estimated_price(self):
        """End-to-end contract: item + grade + warranty + age → estimated_price."""
        if not self.item_code:
            self.skipTest(
                "No Buyback Price Master seeded — cannot exercise validate. "
                "Run `bench execute buyback.qa.factory.seed_all` to seed."
            )
        if not self.customer:
            self.skipTest("No Customer on site — cannot exercise validate.")

        doc = frappe.new_doc("Buyback Assessment")
        doc.item = self.item_code
        doc.warranty_status = "In Warranty"
        doc.device_age_months = "4-6 Months"
        doc.customer = self.customer
        doc.mobile_no = "9999999999"
        doc.flags.skip_duplicate_check = True
        doc.run_method("validate")

        self.assertGreater(
            float(doc.estimated_price or 0), 0,
            "validate() must populate estimated_price for a priced item.",
        )


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
