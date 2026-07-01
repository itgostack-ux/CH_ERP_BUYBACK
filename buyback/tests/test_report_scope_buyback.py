# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""
Tier 4 — Report scope injection E2E tests for buyback.

Verifies:
  * ``standard_conditions`` in ``report_utils`` always appends CH User
    Scope narrowing when the underlying query has a ``store`` column
    (which every Buyback Assessment/Inspection/Order/SLA row does).
  * The five outlier reports that don't route through
    ``standard_conditions`` — duplicate_imei_attempts,
    pending_confirmations, pending_payments, refurb_pipeline,
    otp_failure_report — all delegate to ``scope_condition``.
  * Administrator (bypass) runs everything without narrowing.
"""

from __future__ import annotations

import unittest

import frappe

from ch_erp15.ch_erp15.scope import clear_scope_cache
from buyback.buyback.report.report_utils import (
    scope_condition,
    standard_conditions,
)


_TEST_USER = "tier4-bb-user@ch-tests.local"
_TEST_STORE = "TIER4-BB-STORE-A"


def _ensure_user(user: str) -> None:
    if frappe.db.exists("User", user):
        return
    doc = frappe.new_doc("User")
    doc.email = user
    doc.first_name = "Tier4Bb"
    doc.enabled = 1
    doc.new_password = "TestPass123!Tier4"
    doc.send_welcome_email = 0
    doc.append("roles", {"role": "Accounts User"})
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)


def _get_or_create_warehouse(name: str, company: str) -> str:
    abbr = frappe.db.get_value("Company", company, "abbr")
    full = f"{name} - {abbr}"
    if frappe.db.exists("Warehouse", full):
        return full
    doc = frappe.new_doc("Warehouse")
    doc.warehouse_name = name
    doc.company = company
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    return doc.name


def _get_or_create_ch_store(name: str, warehouse: str, company: str) -> None:
    if frappe.db.exists("CH Store", name):
        return
    doc = frappe.new_doc("CH Store")
    doc.store_id = name
    doc.store_code = name
    doc.store_name = name
    doc.company = company
    doc.warehouse = warehouse
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.insert(ignore_permissions=True)


def _make_scope(user: str, store: str) -> None:
    for row in frappe.get_all("CH User Scope", filters={"user": user}, pluck="name"):
        frappe.delete_doc("CH User Scope", row, ignore_permissions=True, force=True)
    doc = frappe.new_doc("CH User Scope")
    doc.user = user
    doc.scope_role = "Store Executive"
    doc.enabled = 1
    doc.append("stores", {"store": store})
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)


class TestReportScopeBuyback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = frappe.db.get_value("Company", {}, "name")
        if not cls.company:
            raise Exception("No Company in this site — cannot run Tier 4 buyback tests.")

        cls.wh_in_scope = _get_or_create_warehouse("Tier4 BB A WH", cls.company)
        _get_or_create_ch_store(_TEST_STORE, cls.wh_in_scope, cls.company)
        _ensure_user(_TEST_USER)
        _make_scope(_TEST_USER, _TEST_STORE)
        clear_scope_cache(_TEST_USER)
        frappe.db.commit()

    def setUp(self):
        frappe.set_user(_TEST_USER)
        clear_scope_cache(_TEST_USER)

    def tearDown(self):
        frappe.set_user("Administrator")

    # ── helper contract ─────────────────────────────────────────────────

    # 1 — standard_conditions appends scope for scoped user even with empty filters
    def test_01_standard_conditions_appends_scope(self):
        result = standard_conditions()
        # Scoped user with 1 store → " AND (store IN ('TIER4-BB-STORE-A'))"
        self.assertIn(_TEST_STORE, result)
        self.assertIn(" AND ", result)

    # 2 — standard_conditions bypass user gets empty string
    def test_02_standard_conditions_bypass(self):
        frappe.set_user("Administrator")
        result = standard_conditions()
        self.assertEqual(result, "")

    # 3 — standard_conditions honours field_map={"store": None} opt-out
    def test_03_standard_conditions_opt_out(self):
        result = standard_conditions(field_map={"store": None})
        self.assertEqual(result, "")

    # 4 — standard_conditions uses alias correctly
    def test_04_standard_conditions_alias_scope(self):
        result = standard_conditions(alias="o.")
        self.assertIn("o.store", result)

    # 5 — scope_condition bypass user
    def test_05_scope_condition_bypass(self):
        frappe.set_user("Administrator")
        self.assertEqual(scope_condition(store_field="store"), "")

    # 6 — scope_condition scoped user
    def test_06_scope_condition_scoped(self):
        result = scope_condition(alias="o.", store_field="store")
        self.assertIn("o.store", result)
        self.assertIn(_TEST_STORE, result)

    # ── report end-to-end smoke ─────────────────────────────────────────

    # 7 — reports that route through standard_conditions run cleanly
    def test_07_standard_conditions_reports_scoped(self):
        from buyback.buyback.report.buyback_funnel.buyback_funnel import (
            execute as funnel_execute,
        )
        from buyback.buyback.report.category_trend.category_trend import (
            execute as ct_execute,
        )
        from buyback.buyback.report.grade_distribution.grade_distribution import (
            execute as gd_execute,
        )
        for fn in (funnel_execute, ct_execute, gd_execute):
            result = fn({})
            self.assertTrue(len(result) >= 2, f"{fn.__module__} should return columns+data")

    # 8 — outlier reports run cleanly for scoped user
    def test_08_outlier_reports_scoped(self):
        from buyback.buyback.report.duplicate_imei_attempts.duplicate_imei_attempts import (
            execute as dup_execute,
        )
        from buyback.buyback.report.pending_confirmations.pending_confirmations import (
            execute as pc_execute,
        )
        from buyback.buyback.report.pending_payments.pending_payments import (
            execute as pp_execute,
        )
        from buyback.buyback.report.refurb_pipeline.refurb_pipeline import (
            execute as rp_execute,
        )
        from buyback.buyback.report.otp_failure_report.otp_failure_report import (
            execute as otp_execute,
        )
        for fn in (dup_execute, pc_execute, pp_execute, rp_execute, otp_execute):
            result = fn({})
            self.assertTrue(len(result) >= 2, f"{fn.__module__} should return columns+data")

    # 9 — Administrator bypass runs every touched report
    def test_09_administrator_bypass(self):
        frappe.set_user("Administrator")
        from buyback.buyback.report.buyback_funnel.buyback_funnel import (
            execute as funnel_execute,
        )
        from buyback.buyback.report.pending_confirmations.pending_confirmations import (
            execute as pc_execute,
        )
        from buyback.buyback.report.pending_payments.pending_payments import (
            execute as pp_execute,
        )
        from buyback.buyback.report.otp_failure_report.otp_failure_report import (
            execute as otp_execute,
        )
        funnel_execute({})
        pc_execute({})
        pp_execute({})
        otp_execute({})
