"""
Buyback QA – FrappeTestCase tests
===================================
Run via::

    bench --site erpnext.local run-tests --app buyback --module buyback.qa.test_qa_scenarios

Each scenario from the Scenario Library is wrapped as an individual test method
so that failures are granular and pytest/unittest can report per-scenario.
"""

from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from buyback.qa.factory import seed_all, cleanup_all
from buyback.qa.scenarios import get_all_scenarios


class TestBuybackQAScenarios(FrappeTestCase):
    """Run every QA scenario as a separate test method."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        seed_all()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        # Optionally cleanup after all tests
        # cleanup_all()

    def _run_scenario(self, scenario_id: str):
        """Helper to run a single scenario and assert it passes."""
        scenarios = get_all_scenarios()
        scenario = next((s for s in scenarios if s["id"] == scenario_id), None)
        self.assertIsNotNone(scenario, f"Scenario {scenario_id} not found")

        ctx: dict = {"docs": []}
        passed, message = scenario["fn"](ctx)
        self.assertTrue(passed, f"Scenario {scenario_id} failed: {message}")

    def test_s01_happy_path_cash(self):
        self._run_scenario("S01")

    def test_s02_happy_path_upi(self):
        self._run_scenario("S02")

    def test_s03_high_value_approval(self):
        self._run_scenario("S03")

    def test_s04_price_override(self):
        self._run_scenario("S04")

    def test_s05_otp_failure(self):
        self._run_scenario("S05")

    def test_s06_otp_expired(self):
        self._run_scenario("S06")

    def test_s07_device_rejected(self):
        self._run_scenario("S07")

    def test_s08_cancel_after_quote(self):
        self._run_scenario("S08")

    def test_s09_cancel_after_inspection(self):
        self._run_scenario("S09")

    def test_s10_exchange_flow(self):
        self._run_scenario("S10")

    def test_s11_accessories_deduction(self):
        self._run_scenario("S11")

    def test_s12_duplicate_imei(self):
        self._run_scenario("S12")

    def test_s13_unknown_model(self):
        self._run_scenario("S13")

    def test_s14_negative_price(self):
        self._run_scenario("S14")

    def test_s15_double_payout(self):
        self._run_scenario("S15")

    def test_s16_store_permission(self):
        self._run_scenario("S16")

    def test_s17_reporting_sanity(self):
        self._run_scenario("S17")
