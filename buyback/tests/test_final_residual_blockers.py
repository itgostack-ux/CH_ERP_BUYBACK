from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe
from frappe.utils import now_datetime

from buyback import api, hooks, install, public_portal_api, tasks, utils
from buyback.buyback import alerts, sla_engine, whatsapp_notifications
from buyback.buyback.doctype.buyback_order import buyback_order
from buyback.buyback.doctype.buyback_question_bank.buyback_question_bank import BuybackQuestionBank
from buyback.buyback.page.buyback_hub import buyback_hub_api
from buyback.exceptions import BuybackStatusError


class TestFinalResidualBlockers(TestCase):
	def test_public_quote_rate_limit_scopes_same_ip_by_mobile_and_purpose(self):
		identities = []
		with (
			patch.object(public_portal_api, "_public_client_ip", return_value="203.0.113.10"),
			patch.object(public_portal_api, "get_int_setting", side_effect=lambda field, default: default),
			patch.object(
				public_portal_api,
				"increment_fixed_window",
				side_effect=lambda namespace, identity, window: identities.append(
					(namespace, identity, window)
				) or 1,
			),
		):
			public_portal_api._enforce_public_quote_rate_limit("otp", "9876543210")
			public_portal_api._enforce_public_quote_rate_limit("otp", "9876543211")
		self.assertNotEqual(identities[0][1], identities[1][1])
		self.assertTrue(all("Buyback Customer Approval" in row[1] for row in identities))

	def test_public_quote_otp_consumption_is_locked_and_replay_safe(self):
		source = inspect.getsource(public_portal_api.submit_public_quote_request)
		self.assertIn("for_update=True", source)
		self.assertIn('"status": "Pending"', source)
		self.assertIn('pending_otp, "status", "Expired"', source)
		self.assertLess(source.index('pending_otp, "status", "Expired"'), source.index("doc.insert()"))
		self.assertNotIn("ignore_permissions=True", source)
	def test_question_code_lock_failure_is_fail_closed(self):
		doc = frappe._dict(question_code="screen_test", name="NEW-QUESTION")
		with (
			patch(
				"buyback.buyback.doctype.buyback_question_bank.buyback_question_bank.get_buyback_setting_value",
				return_value=10,
			),
			patch.object(frappe.db, "sql", return_value=[(0,)]) as sql,
		):
			with self.assertRaises(frappe.ValidationError):
				BuybackQuestionBank._ensure_unique_question_code(doc)
		self.assertEqual(sql.call_count, 1)

	def test_question_code_suffix_allocation_is_bounded(self):
		doc = frappe._dict(question_code="screen_test", name="NEW-QUESTION")
		module_path = "buyback.buyback.doctype.buyback_question_bank.buyback_question_bank"
		with (
			patch(f"{module_path}.get_buyback_setting_value", side_effect=lambda field, default: 2),
			patch.object(frappe.db, "sql", side_effect=[[(1,)], [(1,)]]),
			patch.object(frappe.db, "get_value", return_value="EXISTING"),
			patch.object(frappe, "get_all", return_value=["screen_test_2", "screen_test_3"]),
			patch.object(frappe.db, "exists", return_value=False),
			patch(f"{module_path}.secrets.token_hex", return_value="abcdef0123456789"),
		):
			BuybackQuestionBank._ensure_unique_question_code(doc)
		self.assertEqual(doc.question_code, "screen_test_abcdef0123456789")
		self.assertNotIn("while", inspect.getsource(BuybackQuestionBank._ensure_unique_question_code))

	def test_customer_approval_and_payout_lock_same_order_row(self):
		token = "valid-token"
		row = frappe._dict(
			name="BB-ORDER-1",
			status="Awaiting Customer Approval",
			creation=now_datetime(),
			approval_token_issued_at=now_datetime(),
			approval_token_digest=hashlib.sha256(token.encode()).hexdigest(),
			customer_approved=0,
			customer_approved_at=None,
		)
		meta = frappe._dict(has_field=lambda fieldname: fieldname == "approval_token_issued_at")
		with (
			patch.object(api.frappe, "get_meta", return_value=meta),
			patch.object(api.frappe.db, "get_value", return_value=row) as get_value,
			patch.object(api, "get_int_setting", return_value=72),
		):
			self.assertEqual(
				api._resolve_token(
					token,
					require_payout_editable=True,
					for_update=True,
				),
				"BB-ORDER-1",
			)
		self.assertTrue(get_value.call_args.kwargs["for_update"])
		self.assertIn("for_update=True", inspect.getsource(api.customer_approve_via_token))
		self.assertIn("for_update=True", inspect.getsource(api.save_customer_payout_preference))

	def test_locked_payout_recheck_rejects_customer_approved_order(self):
		token = "used-token"
		row = frappe._dict(
			name="BB-ORDER-2",
			status="Awaiting Customer Approval",
			creation=now_datetime(),
			approval_token_issued_at=now_datetime(),
			approval_token_digest=hashlib.sha256(token.encode()).hexdigest(),
			customer_approved=1,
			customer_approved_at=now_datetime(),
		)
		meta = frappe._dict(has_field=lambda fieldname: fieldname == "approval_token_issued_at")
		with (
			patch.object(api.frappe, "get_meta", return_value=meta),
			patch.object(api.frappe.db, "get_value", return_value=row) as get_value,
			patch.object(api, "get_int_setting", return_value=72),
		):
			with self.assertRaises(BuybackStatusError):
				api._resolve_token(
					token,
					require_payout_editable=True,
					for_update=True,
				)
		self.assertTrue(get_value.call_args.kwargs["for_update"])

	def test_numeric_external_ids_use_atomic_seeded_series(self):
		with (
			patch.object(utils.frappe.db, "get_value", return_value=41),
			patch.object(utils.frappe.db, "sql") as sql,
			patch.object(utils, "getseries", return_value="0000000042") as getseries,
		):
			self.assertEqual(
				utils.next_numeric_external_id("Buyback Order", "order_id"),
				42,
			)
		self.assertIn("ON DUPLICATE KEY UPDATE", sql.call_args.args[0])
		self.assertIn("GREATEST", sql.call_args.args[0])
		getseries.assert_called_once_with("BUYBACK-ORDER-ID", 10)

		controller_fields = {
			"buyback_question_bank": "question_id",
			"buyback_pricing_rule": "pricing_rule_id",
			"buyback_price_master": "buyback_price_id",
			"buyback_audit_log": "audit_id",
			"buyback_assessment": "assessment_id",
			"buyback_inspection": "inspection_id",
			"grade_master": "grade_id",
			"buyback_checklist_template": "checklist_id",
			"buyback_exchange_order": "exchange_id",
			"buyback_order": "order_id",
		}
		doctype_root = Path(utils.__file__).parent / "buyback" / "doctype"
		for controller, fieldname in controller_fields.items():
			source = (doctype_root / controller / f"{controller}.py").read_text()
			self.assertIn("next_numeric_external_id", source, controller)
			self.assertNotIn(f"MAX({fieldname})", source, controller)

	def test_buyback_hub_requires_configured_role_and_named_reads(self):
		source = inspect.getsource(buyback_hub_api._check_hub_access)
		self.assertIn('"dashboard_roles"', source)
		for doctype in ("Buyback Order", "Buyback Assessment", "Buyback Inspection"):
			self.assertIn(f'"{doctype}"', source)
		self.assertIn('ptype="read"', source)

	def test_buyback_hub_has_no_unrestricted_scope_fallback(self):
		module_source = inspect.getsource(buyback_hub_api)
		self.assertIn("from ch_erp15.ch_erp15.scope import intersect_filters", module_source)
		self.assertNotIn("Fallback if ch_erp15 not available", module_source)
		self.assertIn("_check_hub_access()", inspect.getsource(buyback_hub_api.get_buyback_hub_data))

	def test_store_pickup_notification_fails_closed(self):
		source = inspect.getsource(buyback_order.BuybackOrder._notify_pickup_role)
		self.assertIn("get_scoped_users", source)
		self.assertIn("return", source)
		self.assertNotIn('frappe.get_all(\n                "Has Role"', source)

	def test_buyback_approval_email_uses_company_branding(self):
		source = inspect.getsource(whatsapp_notifications._notify_awaiting_customer_approval)
		self.assertNotIn("Congruence Holdings", source)
		self.assertNotIn("GoGizmo", source)
		self.assertIn('get_cached_value("Company", doc.company, "company_name")', source)
		self.assertIn("escape_html(company_label)", source)

	def test_scheduler_batches_are_configurable_and_have_no_explicit_commit(self):
		functions = (
			(tasks.expire_assessments, "_scheduler_batch_limit"),
			(tasks.expire_otps, "_scheduler_batch_limit"),
			(sla_engine._evaluate_order_slas, '"scheduler_batch_limit"'),
			(sla_engine._evaluate_exchange_slas, '"scheduler_batch_limit"'),
			(sla_engine._evaluate_inspection_slas, '"scheduler_batch_limit"'),
			(alerts._check_cash_limits, '"scheduler_batch_limit"'),
			(alerts._check_conversion_rates, '"scheduler_batch_limit"'),
			(alerts._check_inspection_backlogs, '"scheduler_batch_limit"'),
		)
		for function, limit_marker in functions:
			source = inspect.getsource(function)
			self.assertIn(limit_marker, source)
			self.assertNotIn("frappe.db.commit", source)

	def test_app_screen_access_uses_configured_roles(self):
		self.assertEqual(
			hooks.add_to_apps_screen[0]["has_permission"],
			"buyback.utils.has_app_permission",
		)
		self.assertIn('has_configured_role("app_access_roles"', inspect.getsource(utils.has_app_permission))

	def test_otp_expiry_is_one_set_based_status_update(self):
		source = inspect.getsource(tasks.expire_otps)
		self.assertEqual(source.count("frappe.db.set_value"), 1)
		self.assertNotIn("frappe.get_doc", source)
		self.assertNotIn("savepoint", source)

	def test_scheduler_alerts_share_a_configurable_budget(self):
		self.assertIn("new_scheduler_alert_budget", inspect.getsource(alerts.check_daily_alerts))
		self.assertIn("new_scheduler_alert_budget", inspect.getsource(sla_engine.evaluate_all_slas))
		self.assertIn("claim_scheduler_alert", inspect.getsource(sla_engine._fire_sla_alert))

	def test_single_settings_honor_stored_roles_and_alert_limits(self):
		meta = MagicMock()
		meta.has_field.return_value = True
		stored = {
			"order_operation_roles": "Regional Buyback Lead",
			"scheduler_alert_limit": "7",
			"alert_recipient_limit": "9",
		}
		with (
			patch.object(utils.frappe, "get_meta", return_value=meta),
			patch.object(
				utils.frappe,
				"get_cached_value",
				side_effect=lambda doctype, name, fieldname: stored[fieldname],
			),
		):
			self.assertEqual(
				utils.get_role_setting("order_operation_roles", ("Buyback Admin",)),
				frozenset({"Regional Buyback Lead"}),
			)
			self.assertEqual(utils.new_scheduler_alert_budget(), {"remaining": 7})
			self.assertEqual(alerts._alert_recipient_limit(), 9)
		meta.has_field.assert_any_call("order_operation_roles")

	def test_sla_scheduler_uses_configured_targets_and_rotating_batches(self):
		source = inspect.getsource(sla_engine.evaluate_all_slas)
		self.assertIn("_configured_sla_targets", source)
		for function in (
			sla_engine._evaluate_order_slas,
			sla_engine._evaluate_exchange_slas,
			sla_engine._evaluate_inspection_slas,
		):
			self.assertIn("_rotating_sla_rows", inspect.getsource(function))
		log_source = inspect.getsource(sla_engine._create_sla_log)
		self.assertIn("ON DUPLICATE KEY UPDATE", log_source)
		self.assertNotIn("frappe.cache", log_source)

	def test_buyback_workflows_have_system_manager_parity(self):
		fixture = json.loads((Path(__file__).parents[1] / "fixtures" / "workflow.json").read_text())
		for workflow in fixture:
			state_signatures = {
				(row["state"], str(row["doc_status"]))
				for row in workflow["states"] if row["allow_edit"] != "System Manager"
			}
			system_states = {
				(row["state"], str(row["doc_status"]))
				for row in workflow["states"] if row["allow_edit"] == "System Manager"
			}
			self.assertEqual(state_signatures - system_states, set())

			def signature(row):
				return (row["state"], row["action"], row["next_state"], row.get("condition", ""))

			transitions = {signature(row) for row in workflow["transitions"] if row["allowed"] != "System Manager"}
			system_transitions = {signature(row) for row in workflow["transitions"] if row["allowed"] == "System Manager"}
			self.assertEqual(transitions - system_transitions, set())
		self.assertIn("System Manager", inspect.getsource(install.ensure_workflow_system_manager_parity))

	def test_guest_token_and_public_response_inputs_are_bounded(self):
		self.assertIn("256", inspect.getsource(api._resolve_token))
		self.assertIn("parse_public_response_rows", inspect.getsource(public_portal_api.get_public_quote_estimate))
		self.assertIn("parse_public_response_rows", inspect.getsource(public_portal_api.submit_public_quote_request))
		self.assertIn("public_payload_max_chars", inspect.getsource(utils.parse_public_response_rows))

	def test_release_limit_settings_are_declared(self):
		settings_path = (
			Path(__file__).parents[1]
			/ "buyback"
			/ "doctype"
			/ "buyback_settings"
			/ "buyback_settings.json"
		)
		settings = json.loads(settings_path.read_text())
		fields = {row["fieldname"] for row in settings["fields"]}
		self.assertTrue(
			{
				"scheduler_alert_limit",
				"scheduler_batch_limit",
				"public_payload_max_chars",
				"public_response_row_limit",
				"public_quote_otp_rate_limit",
				"public_quote_submit_rate_limit",
				"public_quote_rate_window_seconds",
				"public_quote_service_user",
				"question_code_lock_timeout_seconds",
				"question_code_suffix_retry_limit",
				"max_payment_rows",
				"store_scorecard_configuration",
				"inspector_scorecard_configuration",
				"executive_scorecard_configuration",
				"scorecard_grade_thresholds",
			}.issubset(fields)
		)
