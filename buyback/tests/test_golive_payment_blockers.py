# Copyright (c) 2026, Congruence Holdings and contributors
# See license.txt

"""Go-live blocker regressions (Sep-01 final testing sweep).

Each class pins one of the four code defects found by the 44-agent go-live
fleet, so a refactor that reintroduces any of them fails loudly:

1. ``record_payment`` failed on EVERY submitted order: the endpoint refreshes
   ``lifecycle_evidence_signature`` and then calls ``doc.save()`` on a
   docstatus-1 document, but the field was not ``allow_on_submit`` — Frappe's
   ``validate_update_after_submit`` threw ``UpdateAfterSubmitError`` before a
   single payment could ever be recorded.
2. The duplicate-IMEI guard compared raw values with exact equality, ran in
   ``before_insert`` (i.e. BEFORE ``validate()``'s alias strip), and the alias
   sync only wrote back stripped values in its copy branches — so a padded
   " <IMEI> " both persisted verbatim and sailed past the guard.
3. The deny-before-write gates (``_require_order_action`` and
   ``require_scoped_document_action``) lost their ``require_configured_role``
   first check: an unauthorised caller was only stopped AFTER the bound
   document had been locked and reloaded — or not stopped at all.
4. The refurb reports granted roles that hold no ``report`` permission on
   their ref doctype (and vice versa), so the report either 404'd for the
   granted role or was invisible to the roles actually working refurbs.

All document writes stay inside the test transaction and are rolled back.
"""

import json
import os
import unittest
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

import buyback
from buyback import utils
from buyback.buyback.doctype.buyback_order import buyback_order

APP_PATH = os.path.dirname(os.path.abspath(buyback.__file__))


def _find_masters():
	"""Resolve the minimal master rows a Buyback Order insert needs.

	The suite builds its own transactional documents but reuses whatever
	masters the site has — a fresh site and a restored dump both qualify —
	so there are no committed fixtures to clean up.
	"""
	store = frappe.db.get_value("Warehouse", {"is_group": 0, "disabled": 0}, "name")
	return {
		"customer": frappe.db.get_value("Customer", {"disabled": 0}, "name"),
		"item": frappe.db.get_value("Item", {"disabled": 0}, "name"),
		"store": store,
		"company": frappe.db.get_value("Warehouse", store, "company") if store else None,
		"grade": frappe.db.get_value("Grade Master", {}, "name"),
	}


def _make_order(masters, imei, final_price=500):
	doc = frappe.get_doc({
		"doctype": "Buyback Order",
		"customer": masters["customer"],
		"mobile_no": "9876500011",
		"store": masters["store"],
		"company": masters["company"],
		"item": masters["item"],
		"condition_grade": masters["grade"],
		"imei_serial": imei,
		"base_price": final_price,
		"final_price": final_price,
		"imei_validation_status": "Verified Clean",
	})
	doc.flags.ch_evidence_update_authorized = True
	doc.insert(ignore_permissions=True)
	return doc


class _MastersMixin:
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.masters = _find_masters()

	def setUp(self):
		if not all(self.masters.values()):
			self.skipTest(f"missing master rows: {self.masters}")

	def tearDown(self):
		frappe.db.rollback()


class TestRecordPaymentOnSubmittedOrder(_MastersMixin, FrappeTestCase):
	"""Defect 1 — record_payment must work on a submitted order."""

	def test_lifecycle_evidence_signature_is_allow_on_submit_in_schema(self):
		"""The schema fix itself: without allow_on_submit, EVERY payment save
		on a submitted order dies in validate_update_after_submit."""
		path = os.path.join(
			APP_PATH, "buyback", "doctype", "buyback_order", "buyback_order.json"
		)
		with open(path) as f:
			schema = json.load(f)
		field = next(
			f for f in schema["fields"]
			if f["fieldname"] == "lifecycle_evidence_signature"
		)
		self.assertEqual(field.get("allow_on_submit"), 1)

	def test_record_payment_succeeds_on_submitted_order(self):
		"""End-to-end: submitted + consented order accepts a payment row.

		A PARTIAL amount is used on purpose — it exercises the failing
		``doc.save()`` (append payment row + refreshed evidence signature on
		a docstatus-1 doc) without entering the mark_paid/JE leg, which
		needs indemnity and company accounting setup this test must not
		depend on.
		"""
		from buyback.api import record_payment

		doc = _make_order(self.masters, "351111222233301")
		doc.submit()
		# Stand-in for the authorised customer_approve()/verify_otp() path —
		# db_set writes the same columns those actions stamp.
		doc.db_set(
			{"customer_approved": 1, "otp_verified": 1, "status": "OTP Verified"},
			update_modified=False,
		)
		doc.reload()

		result = record_payment(doc.name, "Cash", 200)

		self.assertEqual(flt(result["total_paid"]), 200.0)
		self.assertEqual(result["payment_status"], "Partially Paid")
		doc.reload()
		self.assertEqual(len(doc.payments), 1)
		self.assertEqual(flt(doc.payments[0].amount), 200.0)
		# The refreshed evidence signature must have been persisted too.
		self.assertTrue(doc._has_valid_lifecycle_evidence())


class TestImeiWhitespaceDuplicateGuard(_MastersMixin, FrappeTestCase):
	"""Defect 2 — padded IMEIs must be stripped at capture and caught by the guard."""

	IMEI = "351111222233302"

	def test_capture_strips_whitespace_on_both_aliases(self):
		doc = _make_order(self.masters, f"  {self.IMEI}  ")
		self.assertEqual(doc.imei_serial, self.IMEI)
		self.assertEqual(doc.serial_no, self.IMEI)

	def test_padded_incoming_imei_is_caught_as_duplicate(self):
		_make_order(self.masters, self.IMEI)
		with self.assertRaises(frappe.ValidationError):
			_make_order(self.masters, f"  {self.IMEI} ")

	def test_clean_incoming_imei_matches_legacy_padded_row(self):
		"""Rows stored with padding BEFORE the strip fix must still block a
		clean re-capture — the guard compares on TRIM(imei_serial)."""
		doc = _make_order(self.masters, self.IMEI)
		# Simulate a pre-fix legacy row (bypasses validate; rolled back).
		frappe.db.set_value(
			"Buyback Order", doc.name, "imei_serial", f" {self.IMEI} ",
			update_modified=False,
		)
		with self.assertRaises(frappe.ValidationError):
			_make_order(self.masters, self.IMEI)

	def test_distinct_imeis_still_insert(self):
		_make_order(self.masters, self.IMEI)
		other = _make_order(self.masters, "351111222233303")
		self.assertTrue(other.name)


class TestDenyBeforeWriteGates(unittest.TestCase):
	"""Defect 3 — the configured-role gate must refuse before any document
	permission check, lock, reload or write on the bound document."""

	def test_order_action_gate_refuses_before_touching_the_document(self):
		doc = Mock(name="BB-ORDER-GATE-1")
		with patch.object(
			buyback_order,
			"require_configured_role",
			side_effect=frappe.PermissionError("denied"),
		):
			with self.assertRaises(frappe.PermissionError):
				buyback_order._require_order_action(
					doc, "payment_operation_roles", "mark a Buyback order Paid"
				)
		doc.check_permission.assert_not_called()
		doc.reload.assert_not_called()

	def test_scoped_document_gate_refuses_before_touching_the_document(self):
		doc = Mock(name="BBA-GATE-1", doctype="Buyback Assessment")
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
		doc.reload.assert_not_called()


class TestRecordPaymentRefusesUnconfiguredUser(_MastersMixin, FrappeTestCase):
	"""Defect 3, proven end-to-end as a real non-privileged user: the refusal
	must land before any payment row is written."""

	def test_unconfigured_user_is_refused_and_nothing_is_written(self):
		from buyback.api import record_payment
		from buyback.utils import is_privileged_user

		user = "kevin@gmail.com"
		if not frappe.db.exists("User", user) or is_privileged_user(user):
			self.skipTest("needs the known non-privileged scoped user kevin@gmail.com")

		doc = _make_order(self.masters, "351111222233304")
		doc.submit()
		doc.db_set(
			{"customer_approved": 1, "otp_verified": 1, "status": "OTP Verified"},
			update_modified=False,
		)

		frappe.set_user(user)
		try:
			with self.assertRaises(frappe.PermissionError):
				record_payment(doc.name, "Cash", 100)
		finally:
			frappe.set_user("Administrator")

		rows = frappe.get_all(
			"Buyback Order Payment", filters={"parent": doc.name}, pluck="name"
		)
		self.assertEqual(rows, [])


class TestRefurbReportRoleCoherence(unittest.TestCase):
	"""Defect 4 — every role granted on a refurb report must hold the
	``report`` permission on the report's ref doctype, the way erpnext
	pairs Report roles with DocPerms. A role granted only at report level
	sees the report in the menu and then gets refused when running it; a
	report-permitted DocPerm role missing from the report list cannot see
	it at all."""

	REPORTS = (
		("buyback_refurb_queue", "buyback_order"),
		("refurb_pipeline", "refurbishment_order"),
	)

	@staticmethod
	def _load(path_parts):
		with open(os.path.join(APP_PATH, "buyback", *path_parts)) as f:
			return json.load(f)

	def test_report_roles_are_backed_by_ref_doctype_report_docperms(self):
		for report_dir, doctype_dir in self.REPORTS:
			report = self._load(("report", report_dir, f"{report_dir}.json"))
			schema = self._load(("doctype", doctype_dir, f"{doctype_dir}.json"))
			self.assertEqual(
				frappe.scrub(report["ref_doctype"]), doctype_dir,
				f"{report['name']}: ref_doctype drifted from this test's mapping",
			)
			report_perm_roles = {
				p["role"] for p in schema["permissions"]
				if p.get("report") == 1 and not p.get("permlevel")
			}
			granted = {r["role"] for r in report["roles"]}
			self.assertTrue(
				granted, f"{report['name']}: no roles granted at all"
			)
			self.assertLessEqual(
				granted, report_perm_roles,
				f"{report['name']}: roles {sorted(granted - report_perm_roles)} are "
				"granted on the report but hold no 'report' DocPerm on "
				f"{report['ref_doctype']}",
			)

	def test_refurb_queue_no_longer_grants_dangling_roles(self):
		report = self._load(("report", "buyback_refurb_queue", "buyback_refurb_queue.json"))
		granted = {r["role"] for r in report["roles"]}
		# "Buyback User" is not part of the buyback role model and Stock
		# Manager holds no DocPerm on Buyback Order — both grants were dead.
		self.assertNotIn("Buyback User", granted)
		self.assertNotIn("Stock Manager", granted)


if __name__ == "__main__":
	unittest.main()
