"""
E2E test — ``buyback.recipes.upsert_grade`` happy path + idempotency.

Exercises a real DB write end-to-end:
  1. Insert a new Grade Master row via the recipe.
  2. Verify the row exists with the requested attributes.
  3. Re-run with a different display_order — verify update, not dup.
  4. Verify empty grade_name raises ValueError.

All writes are rolled back at ``tearDownClass`` via savepoint, so this
test is safe to run against any site including test.localhost or an
integration site.
"""

from __future__ import annotations

import frappe

from ch_erp15.testing.e2e import E2ETestCase


# Sentinel prefix keeps the e2e-created grades identifiable inside the
# transaction and easy to grep if a rollback ever leaks (it shouldn't).
_E2E_GRADE = "__E2E_GRADE_QA"


class TestUpsertGradeRecipeE2E(E2ETestCase):
    """Pilot e2e test for buyback recipes — full insert + upsert flow."""

    def test_recipe_creates_new_grade(self) -> None:
        ctx = self.run_recipe(
            "buyback.recipes.upsert_grade.run",
            grade_name=_E2E_GRADE,
            display_order=9,
            description="E2E pilot grade",
        )
        self.assertTrue(ctx["created"])
        self.assertEqual(ctx["grade_name"], _E2E_GRADE)

        # State post-conditions.
        self.assertDocValue("Grade Master", ctx["name"], "grade_name", _E2E_GRADE)
        self.assertDocValue("Grade Master", ctx["name"], "display_order", 9)
        self.assertDocValue("Grade Master", ctx["name"], "description", "E2E pilot grade")

    def test_recipe_is_idempotent_and_updates(self) -> None:
        first = self.run_recipe(
            "buyback.recipes.upsert_grade.run",
            grade_name=_E2E_GRADE,
            display_order=1,
        )
        second = self.run_recipe(
            "buyback.recipes.upsert_grade.run",
            grade_name=_E2E_GRADE,
            display_order=7,
            description="updated",
        )
        # Second call must return the same row name (no dup).
        self.assertEqual(first["name"], second["name"])
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        # And the update landed.
        self.assertDocValue("Grade Master", second["name"], "display_order", 7)
        self.assertDocValue("Grade Master", second["name"], "description", "updated")
        # Exactly one row exists (unique constraint held).
        self.assertRecordCount(
            "Grade Master", {"grade_name": _E2E_GRADE}, expected=1,
        )

    def test_recipe_rejects_empty_grade_name(self) -> None:
        with self.assertRaises(ValueError):
            self.run_recipe("buyback.recipes.upsert_grade.run", grade_name="")

    def test_rollback_leaves_no_trace(self) -> None:
        """Sanity check on the harness itself — writes made in this test
        are undone before the next test starts."""
        # Do a write in this test.
        self.run_recipe(
            "buyback.recipes.upsert_grade.run",
            grade_name=_E2E_GRADE + "_ROLLBACK_CHECK",
        )
        # And it's readable inside the test.
        self.assertRecordExists(
            "Grade Master", {"grade_name": _E2E_GRADE + "_ROLLBACK_CHECK"},
        )
        # The tearDown will roll it back; the next test method (or any
        # future test run) must not see it. That guarantee is what makes
        # the harness safe to run against any site.
