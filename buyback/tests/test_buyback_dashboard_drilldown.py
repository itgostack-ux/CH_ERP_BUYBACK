# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""Every buyback dashboard card must open onto the rows it counted.

This site carries almost no buyback data — 0 Orders, 0 SLA Logs — so asserting
against what is there would only ever prove 0 == 0. Each test seeds a small,
deliberately awkward population inside a savepoint, checks the card against the
list, and rolls back.

The population is chosen to catch the two traps already living in these
dashboards:

  * Store's pending_approvals / pending_payments / pending_pickups are NOT
    date-filtered, so rows are seeded OUTSIDE the range on screen. A drill-down
    that applied the range would come back short.
  * Finance's pending_payouts counts the same statuses as Store's
    pending_payments but IS date-filtered, so the same rows must give different
    answers on the two screens.
"""

from __future__ import annotations

import unittest

import frappe
from frappe.utils import add_days, nowdate

from buyback.buyback.dashboard_api import (
    BUYBACK_TILES,
    CURRENCY_TILES,
    get_dashboard_tile_rows,
    get_finance_dashboard,
    get_store_dashboard,
)

_STORE = "DRILLDOWN-TEST-WH"


def _make_order(store, status, *, days_ago=1, paid=0.0, mode=None, settlement="Buyback"):
    doc = frappe.new_doc("Buyback Order")
    doc.store = store
    doc.status = status
    doc.final_price = 1000
    doc.total_paid = paid
    if mode:
        doc.customer_payout_mode = mode
    if settlement is not None and doc.meta.has_field("settlement_type"):
        doc.settlement_type = settlement
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.flags.ignore_validate = True
    doc.flags.ignore_links = True
    doc.insert(ignore_permissions=True)
    # `status` is a Select whose only option is Draft — the workflow supplies
    # the rest — so insert() coerces anything else straight back to Draft.
    # Write the state we are actually testing, and the age, underneath it.
    frappe.db.set_value(
        "Buyback Order", doc.name,
        {"status": status, "creation": f"{add_days(nowdate(), -days_ago)} 10:00:00"},
        update_modified=False)
    return doc.name


class TestBuybackDashboardDrilldown(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        if not frappe.db.exists("DocType", "Buyback Order"):
            raise unittest.SkipTest("buyback is not installed")
        cls.store = frappe.db.get_value("Warehouse", {"is_group": 0, "disabled": 0}, "name")
        if not cls.store:
            raise unittest.SkipTest("No ledger warehouse to hang the fixture on.")

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("drilldown")
        self.fd, self.td = add_days(nowdate(), -7), nowdate()
        # inside the window
        _make_order(self.store, "Paid", days_ago=2, paid=900, mode="Cash")
        _make_order(self.store, "Closed", days_ago=3, paid=800, mode="UPI")
        _make_order(self.store, "Draft", days_ago=1)
        _make_order(self.store, "Approved", days_ago=2, paid=0)
        # OUTSIDE the window — only the undated cards may see these
        _make_order(self.store, "Awaiting Approval", days_ago=400)
        _make_order(self.store, "OTP Verified", days_ago=400, paid=0)

    def tearDown(self):
        frappe.db.rollback(save_point="drilldown")
        frappe.set_user("Administrator")

    def _rows(self, dashboard, tile):
        return get_dashboard_tile_rows(
            dashboard=dashboard, tile=tile, store=self.store,
            from_date=self.fd, to_date=self.td)

    def test_every_store_card_opens_onto_its_own_number(self):
        kpis = get_store_dashboard(store=self.store, from_date=self.fd, to_date=self.td)["kpis"]
        bad = []
        for (dash, tile) in BUYBACK_TILES:
            if dash != "store" or tile not in kpis:
                continue
            r = self._rows("store", tile)
            money_field = CURRENCY_TILES.get((dash, tile))
            if money_field:
                listed = sum(float(x.get(money_field) or 0) for x in r["rows"])
                if abs(listed - float(kpis[tile] or 0)) > 0.01:
                    bad.append(f"{tile}: card Rs{kpis[tile]} vs rows Rs{listed}")
            elif kpis[tile] != r["total"]:
                bad.append(f"{tile}: card {kpis[tile]} vs rows {r['total']}")
        self.assertFalse(bad, "Store cards disagreeing with their list:\n  " + "\n  ".join(bad))

    def test_undated_cards_still_see_rows_outside_the_range(self):
        """The trap: these three ignore the date filter on screen."""
        self.assertGreaterEqual(
            self._rows("store", "pending_approvals")["total"], 1,
            "pending_approvals must include the 400-day-old order — it is not date-filtered",
        )
        self.assertGreaterEqual(self._rows("store", "pending_payments")["total"], 1)

    def test_finance_pending_is_date_filtered_where_store_is_not(self):
        """Same statuses, two screens, deliberately different populations."""
        store_rows = self._rows("store", "pending_payments")["total"]
        fin_rows = get_dashboard_tile_rows(
            dashboard="finance", tile="pending_count",
            from_date=self.fd, to_date=self.td)["total"]
        self.assertGreater(
            store_rows, fin_rows,
            "the 400-day-old unpaid order must count on Store (undated) "
            "but not on Finance (dated)",
        )

    def test_finance_cards_open_onto_their_own_numbers(self):
        fin = get_finance_dashboard(from_date=self.fd, to_date=self.td)
        kpis = fin.get("kpis", fin)
        bad = []
        for (dash, tile) in BUYBACK_TILES:
            if dash != "finance" or tile not in kpis:
                continue
            r = get_dashboard_tile_rows(dashboard="finance", tile=tile,
                                        from_date=self.fd, to_date=self.td)
            money_field = CURRENCY_TILES.get((dash, tile))
            if money_field:
                listed = sum(float(x.get(money_field) or 0) for x in r["rows"])
                if abs(listed - float(kpis[tile] or 0)) > 0.01:
                    bad.append(f"{tile}: card Rs{kpis[tile]} vs rows Rs{listed}")
            elif kpis[tile] != r["total"]:
                bad.append(f"{tile}: card {kpis[tile]} vs rows {r['total']}")
        self.assertFalse(bad, "Finance cards disagreeing with their list:\n  " + "\n  ".join(bad))

    def test_an_unknown_card_is_refused_with_a_usable_message(self):
        with self.assertRaises(frappe.ValidationError) as caught:
            get_dashboard_tile_rows(dashboard="store", tile="no_such_card", store=self.store)
        self.assertIn("refresh", frappe.utils.strip_html(str(caught.exception)).lower())
