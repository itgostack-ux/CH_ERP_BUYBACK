"""TC_048: Verify buyback amount deduction based on device age.

Two entry points:

* :class:`TestTC048PricingByAge` — a :class:`FrappeTestCase` picked up by
  ``bench run-tests --app buyback``. The setUp phase raises
  :class:`unittest.SkipTest` when the required Buyback Price Master row is
  absent so CI counts the skip separately from a pass.
* :func:`run` — a thin ``bench execute`` shim that reuses the class and
  translates its result into a printable summary. Kept so operator recipes
  and legacy CI jobs invoking
  ``bench execute buyback.buyback.tests.test_tc_048.run`` keep working.

Historical bug this fixes
-------------------------
The original script emitted ``print("SKIP …")`` when the price master row
was missing and then returned ``None`` — exit code 0. In CI the run was
indistinguishable from a passing test, so the whole "prices decrease with
age" invariant silently stopped being verified whenever seed drift
occurred. The class-based path forces every skip to surface as a
countable ``S`` (or full test-runner skip line) rather than a green pass.
"""

from __future__ import annotations

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase


ITEM = "I04508"
AGE_LABELS = ("0-3 Months", "4-6 Months", "7-11 Months", "12+ Months")


def _load_price_master(item: str) -> dict | None:
    return frappe.db.get_value(
        "Buyback Price Master", {"item_code": item},
        ["a_grade_iw_0_3", "a_grade_iw_0_6", "a_grade_iw_6_11", "a_grade_oow_11"],
        as_dict=True,
    )


class TestTC048PricingByAge(FrappeTestCase):
    """Grade-A / In-Warranty prices for ITEM must decrease as device age
    increases, and the pricing engine must match the Buyback Price Master
    row within a ₹50 tolerance."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bpm = _load_price_master(ITEM)
        if not cls.bpm:
            raise unittest.SkipTest(
                f"No Buyback Price Master for {ITEM} — seed drift. "
                "Run `bench execute buyback.qa.factory.seed_all` or "
                "restore the price master for this item to re-enable TC_048."
            )
        cls.grade_a = frappe.db.get_value("Grade Master", {"grade_name": "A"}, "name")
        if not cls.grade_a:
            raise unittest.SkipTest(
                "Grade Master 'A' missing — after_install seed not run. "
                "See buyback/install.py:_seed_grades."
            )
        cls.expected = {
            "0-3 Months":  float(cls.bpm.a_grade_iw_0_3),
            "4-6 Months":  float(cls.bpm.a_grade_iw_0_6),
            "7-11 Months": float(cls.bpm.a_grade_iw_6_11),
            "12+ Months":  float(cls.bpm.a_grade_oow_11),
        }

    def _engine_price(self, age_label: str) -> float:
        from buyback.buyback.pricing.engine import calculate_estimated_price
        r = calculate_estimated_price(
            item_code=ITEM,
            grade=self.grade_a,
            warranty_status="In Warranty",
            device_age_months=age_label,
            responses=[],
            diagnostic_tests=[],
        )
        return float(r.get("estimated_price") or 0)

    def test_engine_matches_price_master_by_age(self):
        for age_label, exp in self.expected.items():
            if exp <= 0:
                # Price master column blank for this bucket — nothing to compare.
                continue
            got = self._engine_price(age_label)
            self.assertAlmostEqual(
                got, exp, delta=50,
                msg=f"[{age_label}] engine={got} vs price-master={exp}",
            )

    def test_prices_decrease_with_age(self):
        prices = [self._engine_price(lbl) for lbl in AGE_LABELS]
        for i in range(len(prices) - 1):
            self.assertGreaterEqual(
                prices[i], prices[i + 1],
                msg=f"Prices must decrease with age but got {prices}",
            )

    def test_server_side_validate_sets_estimated_price(self):
        cust = frappe.db.get_value("Customer", {}, "name")
        if not cust:
            self.skipTest("No Customer on site — cannot exercise Buyback Assessment.validate.")
        doc = frappe.new_doc("Buyback Assessment")
        doc.item = ITEM
        doc.warranty_status = "In Warranty"
        doc.device_age_months = "4-6 Months"
        doc.customer = cust
        doc.mobile_no = "9999999999"
        doc.flags.skip_duplicate_check = True
        doc.run_method("validate")
        got = float(doc.estimated_price or 0)
        exp = float(self.bpm.a_grade_iw_0_6)
        if exp <= 0:
            self.skipTest("Price-master a_grade_iw_0_6 blank — nothing to compare.")
        self.assertAlmostEqual(
            got, exp, delta=50,
            msg=f"server validate estimated_price={got}, expected ~{exp}",
        )


# ── bench execute back-compat shim ──────────────────────────────────────

def run():
    """Kept for legacy ``bench execute buyback.buyback.tests.test_tc_048.run``.

    Delegates to the class-based test and returns a summary dict so operator
    runbooks keep the same shape. Raises AssertionError on failure so
    ``bench execute`` exits non-zero — matching pre-existing behaviour, but
    now driven by real assertions instead of a hand-rolled ``ok`` flag.
    """
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestTC048PricingByAge)
    result = unittest.TextTestRunner(verbosity=2, stream=None).run(suite)
    summary = {
        "pass": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
        "fail": len(result.failures) + len(result.errors),
        "skip": len(result.skipped),
    }
    print("\n=== TC_048 summary:", summary, "===")
    if summary["fail"]:
        raise AssertionError(
            f"TC_048 failed: {summary['fail']} failure(s). "
            "Re-run with `bench run-tests --app buyback --module "
            "buyback.buyback.tests.test_tc_048` for details."
        )
    return summary
