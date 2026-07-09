"""
Contract test — ``buyback.buyback.page.buyback_hub.buyback_hub_api.get_buyback_hub_data`` v1.

This is the Buyback Hub dashboard's data source. Every UI card / table on
the hub reads from this response, so its shape is a hard integration
contract — breaking it silently would break the hub UI.

Pinned:

* Top-level keys: pipeline, kpis, recent_orders, pending_action,
  recent_assessments, recent_inspections, brand_summary, ai_insights,
  financial_control.
* Element shape for every list (SQL-driven, so structure is stable).
* Nested key set for each dict.

The one field NOT pinned tightly is ``ai_insights`` — its item shape
varies with rule outcomes (the "on track" case emits 3 keys, the "high
rejection" case emits 4). Escape-hatched via ``"<any>"``. If we ever
guarantee a uniform shape for AI insights, pin it here and bump to v2.

Runs with **no seeding**. Whatever data lives on the site (0 rows or
1000) produces the same shape, so we can safely run this against
test.localhost or an integration site.

Self-contained: nothing depends on erpnext's test-record cascade.
"""

from __future__ import annotations

import frappe

from ch_erp15.testing.contract import ContractTestCase


class TestGetBuybackHubDataV1(ContractTestCase):
    """Pin the v1 contract of the Buyback Hub dashboard API."""

    FIXTURES_DIR = "buyback/tests/contract/fixtures"

    def setUp(self) -> None:
        super().setUp()
        frappe.set_user("Administrator")

    def test_shape_with_no_filters(self) -> None:
        """Default invocation — no company/store/date filters."""
        from buyback.buyback.page.buyback_hub.buyback_hub_api import get_buyback_hub_data

        resp = get_buyback_hub_data()
        self.assertResponseShape(resp, self.load_fixture("get_buyback_hub_data.v1.json"))

    def test_shape_with_date_filter(self) -> None:
        """Date filter should not change response shape."""
        from buyback.buyback.page.buyback_hub.buyback_hub_api import get_buyback_hub_data

        resp = get_buyback_hub_data(from_date="2024-01-01", to_date="2024-12-31")
        self.assertResponseShape(resp, self.load_fixture("get_buyback_hub_data.v1.json"))

    def test_pipeline_has_all_seven_stages(self) -> None:
        """The pipeline array's length is part of the contract."""
        from buyback.buyback.page.buyback_hub.buyback_hub_api import get_buyback_hub_data

        resp = get_buyback_hub_data()
        stages = [row["key"] for row in resp["pipeline"]]
        self.assertEqual(
            stages,
            ["draft", "otp", "cust_appr", "approved", "paid", "closed", "rejected"],
            msg="Pipeline stages are part of the v1 contract — bump to v2 if this changes.",
        )

    def test_kpis_has_all_eight_metrics(self) -> None:
        """KPI keys are part of the contract."""
        from buyback.buyback.page.buyback_hub.buyback_hub_api import get_buyback_hub_data

        resp = get_buyback_hub_data()
        keys = [row["key"] for row in resp["kpis"]]
        self.assertEqual(
            keys,
            ["today", "active", "total_value", "mtd", "avg_order",
             "assessments", "inspections", "total"],
            msg="KPI keys are part of the v1 contract — bump to v2 if this changes.",
        )

    def test_financial_control_keys(self) -> None:
        """financial_control block is consumed by print / summary; keys pinned."""
        from buyback.buyback.page.buyback_hub.buyback_hub_api import get_buyback_hub_data

        resp = get_buyback_hub_data()
        self.assertEqual(
            set(resp["financial_control"].keys()),
            {"total_buyback_value", "approval_rate", "avg_order_value", "rejection_rate"},
        )
