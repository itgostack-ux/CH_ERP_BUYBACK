"""Regression checks for trusted system-generated JE bypass in buyback.

Run:
    bench --site erpnext.local execute buyback.tests.test_journal_entry_bypass.run
"""

from __future__ import annotations

import frappe

from buyback.buyback.doctype.buyback_order.buyback_order import BuybackOrder


class _FakeJE:
	def __init__(self):
		self.flags = frappe._dict()
		self.user_remark = None
		self.accounts = []
		self.name = None
		self.company = None
		self.posting_date = None

	def insert(self, ignore_permissions=False):
		self.name = "ACC-JV-BUYBACK-0001"
		return self

	def submit(self):
		return self


class _DummyOrder:
	def __init__(self):
		self.name = "BBO-TEST-0001"
		self.item_name = "Test Phone"
		self.item = "ITEM-TEST"
		self.company = "BestBuy Mobiles Pvt Ltd"
		self.final_price = 2500
		self.payout_mode = "Cash"
		self.flags = frappe._dict()
		self.journal_entry = None
		self._db_set = {}

	def get(self, key, default=None):
		return getattr(self, key, default)

	def db_set(self, key, value):
		self._db_set[key] = value
		setattr(self, key, value)


def run():
	orig_get_doc = frappe.get_doc
	orig_get_single = frappe.get_single
	orig_get_value = frappe.db.get_value

	try:
		captured = {}

		def _fake_get_single(doctype):
			if doctype != "Buyback Settings":
				raise AssertionError(f"Unexpected single doctype: {doctype}")
			return frappe._dict(
				buyback_expense_account="Buyback Expense - BM",
				default_company="BestBuy Mobiles Pvt Ltd",
			)

		def _fake_get_value(doctype, filters=None, fieldname=None, *args, **kwargs):
			if doctype == "Company" and fieldname in {
				"default_cash_account",
				"default_bank_account",
				"default_payable_account",
				"cost_center",
			}:
				return {
					"default_cash_account": "Cash - BM",
					"default_bank_account": "Bank - BM",
					"default_payable_account": "Payable - BM",
					"cost_center": "Main - BM",
				}[fieldname]
			return orig_get_value(doctype, filters, fieldname, *args, **kwargs)

		def _fake_get_doc(arg, *args, **kwargs):
			if isinstance(arg, dict) and arg.get("doctype") == "Journal Entry":
				je = _FakeJE()
				je.company = arg.get("company")
				je.posting_date = arg.get("posting_date")
				je.user_remark = arg.get("user_remark")
				je.accounts = list(arg.get("accounts") or [])
				captured["je"] = je
				return je
			return orig_get_doc(arg, *args, **kwargs)

		frappe.get_single = _fake_get_single
		frappe.db.get_value = _fake_get_value
		frappe.get_doc = _fake_get_doc

		order = _DummyOrder()
		BuybackOrder._create_journal_entry(order)

		je = captured.get("je")
		if not je:
			raise AssertionError("Expected buyback Journal Entry to be created")
		if not je.flags.get("ch_system_generated_je"):
			raise AssertionError("Buyback JE did not set trusted system-generated flag")
		if not order.journal_entry:
			raise AssertionError("Buyback order did not store journal entry reference")
		if je.user_remark != "Buyback Order BBO-TEST-0001 — Test Phone":
			raise AssertionError(f"Unexpected JE remark: {je.user_remark}")

		print("[PASS] Buyback JE sets trusted system flag before submit")
		print("Buyback Journal Entry bypass regression: ALL PASS")
	finally:
		frappe.get_doc = orig_get_doc
		frappe.get_single = orig_get_single
		frappe.db.get_value = orig_get_value
