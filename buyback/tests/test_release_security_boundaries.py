"""Negative security regression tests for the Buyback release boundaries."""

from __future__ import annotations

import hashlib
import json
import unittest
from datetime import timedelta
from unittest.mock import Mock, patch

import frappe
from frappe.utils import now_datetime

from buyback import api, exchange_lifecycle, utils
from buyback.buyback import scorecards
from buyback.buyback.doctype.refurbishment_order import refurbishment_order
from buyback.buyback.doctype.buyback_order import buyback_order
from buyback.exceptions import BuybackStatusError


class TestBuybackReleaseSecurityBoundaries(unittest.TestCase):
    def test_administrator_bypass_is_immutable(self):
        self.assertTrue(utils.is_privileged_user("Administrator"))

    def test_system_manager_bypass_is_immutable(self):
        with patch.object(frappe, "get_roles", return_value=["System Manager"]):
            self.assertTrue(utils.is_privileged_user("manager@example.com"))

    def test_unknown_role_action_fails_closed(self):
        with patch.object(frappe, "get_roles", return_value=[]):
            with self.assertRaises(frappe.PermissionError):
                utils.require_configured_role("not_a_real_action", user="user@example.com")

    def test_empty_scope_generates_no_rows_clause(self):
        with patch.object(
            utils,
            "get_buyback_data_scope",
            return_value={"bypass": False, "stores": set(), "warehouses": set(), "companies": set()},
        ):
            clause, params = utils.build_buyback_scope_sql(store_field="o.store")
        self.assertEqual(clause, "1=0")
        self.assertEqual(params, {})

    def test_scope_values_are_bound_parameters(self):
        malicious_store = "STORE' OR 1=1 --"
        with patch.object(
            utils,
            "get_buyback_data_scope",
            return_value={
                "bypass": False,
                "stores": {malicious_store},
                "warehouses": set(),
                "companies": set(),
            },
        ):
            clause, params = utils.build_buyback_scope_sql(store_field="o.store")
        self.assertNotIn(malicious_store, clause)
        self.assertIn(malicious_store, params.values())

    def test_scorecard_company_filter_is_parameterized(self):
        malicious_company = "ACME' OR 1=1 --"
        with (
            patch.object(scorecards, "_require_scorecard_access"),
            patch.object(scorecards, "get_int_setting", return_value=366),
            patch.object(scorecards, "assert_buyback_scope"),
            patch.object(scorecards, "build_buyback_scope_sql", return_value=("1=1", {})),
            patch.object(frappe.db, "sql", return_value=[]) as sql,
        ):
            result = scorecards.get_store_scorecards(company=malicious_company)

        query, params = sql.call_args.args[:2]
        self.assertEqual(result, [])
        self.assertNotIn(malicious_company, query)
        self.assertEqual(params["company"], malicious_company)

    def test_store_scorecard_uses_batched_queries(self):
        store_row = frappe._dict(
            store="STORE-A",
            total_orders=4,
            paid_orders=3,
            total_payout=3000,
            rejected=1,
        )
        assessment_row = frappe._dict(store="STORE-A", cnt=5)
        with (
            patch.object(scorecards, "_require_scorecard_access"),
            patch.object(scorecards, "get_int_setting", return_value=366),
            patch.object(scorecards, "get_buyback_setting_value", return_value=None),
            patch.object(scorecards, "build_buyback_scope_sql", return_value=("1=1", {})),
            patch.object(
                frappe.db,
                "sql",
                side_effect=[[store_row], [assessment_row]],
            ) as sql,
        ):
            result = scorecards.get_store_scorecards()

        self.assertEqual(len(result), 1)
        self.assertEqual(sql.call_count, 2)

    def test_question_loader_batches_categories_and_options(self):
        questions = [
            frappe._dict(
                name="Q-1", question_id=1, question_text="One",
                question_code="ONE", question_type="Select", display_order=1,
                is_mandatory=1, applies_to_category=None,
            ),
            frappe._dict(
                name="Q-2", question_id=2, question_text="Two",
                question_code="TWO", question_type="Select", display_order=2,
                is_mandatory=0, applies_to_category=None,
            ),
        ]
        categories = [frappe._dict(parent="Q-1", item_group="Phones")]
        options = [
            frappe._dict(
                parent="Q-1", option_label="Yes", option_value="yes",
                price_impact_percent=0, is_default=1, idx=1,
            ),
            frappe._dict(
                parent="Q-2", option_label="No", option_value="no",
                price_impact_percent=5, is_default=0, idx=1,
            ),
        ]
        with (
            patch.object(frappe, "get_list", return_value=questions) as get_list,
            patch.object(
                frappe,
                "get_all",
                side_effect=[categories, options],
            ) as get_all,
        ):
            result = api.get_questions(category="Phones")

        self.assertEqual([row["name"] for row in result], ["Q-1", "Q-2"])
        self.assertEqual(get_list.call_count, 1)
        self.assertEqual(get_all.call_count, 2)

    def test_mobile_diagnostic_mapping_uses_one_question_lookup(self):
        diagnostics = [
            {"code": "CAM", "status": "Pass"},
            {"code": "BAT", "status": "Fail"},
            {"code": "UNKNOWN", "status": "Pass"},
        ]
        with patch.object(
            frappe,
            "get_all",
            return_value=["CAM", "BAT"],
        ) as get_all:
            result = api._map_diagnostic_to_responses(diagnostics)

        self.assertEqual(get_all.call_count, 1)
        self.assertEqual(
            result,
            [
                {"question_code": "CAM", "answer_value": "yes"},
                {"question_code": "BAT", "answer_value": "no"},
            ],
        )

    def test_refurbishment_creation_denies_before_loading_return(self):
        with (
            patch.object(
                refurbishment_order,
                "require_configured_role",
                side_effect=frappe.PermissionError("denied"),
            ),
            patch.object(frappe, "get_doc") as get_doc,
        ):
            with self.assertRaises(frappe.PermissionError):
                refurbishment_order.create_from_return("RETURN-1", items=[{"item_code": "ITEM-1"}])
        get_doc.assert_not_called()

    def test_exchange_creation_denies_before_loading_assessment(self):
        with (
            patch.object(
                exchange_lifecycle,
                "require_configured_role",
                side_effect=frappe.PermissionError("denied"),
            ),
            patch.object(frappe, "get_doc") as get_doc,
        ):
            with self.assertRaises(frappe.PermissionError):
                exchange_lifecycle.ensure_exchange_order_from_assessment("ASSESSMENT-1")
        get_doc.assert_not_called()

    def test_bound_order_mutation_denies_before_document_write(self):
        doc = Mock(name="BB-ORDER-1")
        with patch.object(
            buyback_order,
            "require_configured_role",
            side_effect=frappe.PermissionError("denied"),
        ):
            with self.assertRaises(frappe.PermissionError):
                buyback_order._require_order_action(
                    doc, "otp_bypass_roles", "bypass customer OTP"
                )
        doc.check_permission.assert_not_called()

    def test_scoped_document_action_denies_before_document_write(self):
        doc = Mock(name="BBA-1", doctype="Buyback Assessment")
        with patch.object(
            utils,
            "require_configured_role",
            side_effect=frappe.PermissionError("denied"),
        ):
            with self.assertRaises(frappe.PermissionError):
                utils.require_scoped_document_action(
                    doc, "assessment_operation_roles", "update an assessment"
                )
        doc.check_permission.assert_not_called()

    def test_expired_approval_token_is_rejected(self):
        token = "expired-token"
        issued_at = now_datetime() - timedelta(hours=73)
        token_row = frappe._dict(
            name="BB-ORDER-1",
            status="Awaiting Customer Approval",
            creation=issued_at,
            approval_token_issued_at=issued_at,
            approval_token_digest=hashlib.sha256(token.encode()).hexdigest(),
            customer_approved=0,
            customer_approved_at=None,
        )
        meta = frappe._dict(has_field=lambda _fieldname: True)
        with (
            patch.object(frappe, "get_meta", return_value=meta),
            patch.object(frappe.db, "get_value", return_value=token_row),
            patch.object(api, "get_int_setting", return_value=72),
        ):
            with self.assertRaises(BuybackStatusError):
                api._resolve_token(token)

    def test_terminal_order_token_is_rejected(self):
        token = "terminal-token"
        token_row = frappe._dict(
            name="BB-ORDER-2",
            status="Paid",
            creation=now_datetime(),
            approval_token_issued_at=now_datetime(),
            approval_token_digest=hashlib.sha256(token.encode()).hexdigest(),
            customer_approved=1,
            customer_approved_at=now_datetime(),
        )
        meta = frappe._dict(has_field=lambda _fieldname: True)
        with (
            patch.object(frappe, "get_meta", return_value=meta),
            patch.object(frappe.db, "get_value", return_value=token_row),
        ):
            with self.assertRaises(BuybackStatusError):
                api._resolve_token(token)

    def test_guest_approval_payload_masks_bank_and_kyc_values(self):
        order = frappe._dict(
            name="BB-ORDER-3",
            order_id="ORDER-3",
            customer_name="Customer",
            item="ITEM-1",
            brand="Brand",
            imei_serial="123456789012345",
            condition_grade="GRADE-A",
            final_price=1000,
            store="STORE-A",
            status="Awaiting Customer Approval",
            device_photo_front=None,
            device_photo_back=None,
            otp_verified=0,
            warranty_status="In Warranty",
            mobile_no="9876543210",
            customer_payout_mode="Bank Transfer",
            customer_cash_receiver_name="Private Receiver",
            customer_upi_id="secret.person@bank",
            customer_bank_account_holder="Secret Person",
            customer_bank_account_number="998877665544",
            customer_bank_ifsc="BANK0001234",
            customer_bank_name="Bank",
            customer_payout_updated_at=None,
            customer_id_type="PAN",
            customer_id_number="ABCDE1234F",
            kyc_verified=1,
            kyc_verified_at=None,
        )

        with (
            patch.object(api, "_resolve_token", return_value=order.name),
            patch.object(frappe, "get_doc", return_value=order),
            patch.object(frappe.db, "get_value", side_effect=["Item Name", "Grade A", "Store A"]),
        ):
            payload = api.get_buyback_approval_details("active-token")

        serialized = json.dumps(payload)
        for secret in (
            "secret.person@bank",
            "Private Receiver",
            "Secret Person",
            "998877665544",
            "BANK0001234",
            "ABCDE1234F",
        ):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("customer_bank_account_number", payload)
        self.assertNotIn("customer_id_number", payload)

        audit_snapshot = api._payout_audit_snapshot(
            {
                "customer_payout_mode": order.customer_payout_mode,
                "customer_cash_receiver_name": order.customer_cash_receiver_name,
                "customer_upi_id": order.customer_upi_id,
                "customer_bank_account_holder": order.customer_bank_account_holder,
                "customer_bank_account_number": order.customer_bank_account_number,
                "customer_bank_ifsc": order.customer_bank_ifsc,
                "customer_bank_name": order.customer_bank_name,
                "customer_payout_notes": "private note",
            }
        )
        audit_json = json.dumps(audit_snapshot)
        self.assertNotIn(order.customer_bank_account_number, audit_json)
        self.assertNotIn("private note", audit_json)


if __name__ == "__main__":
    unittest.main()
