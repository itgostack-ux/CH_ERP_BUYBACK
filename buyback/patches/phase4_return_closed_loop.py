"""Phase 4 - Return / refund / refurb closed loop.

Backfill Store Credit Wallet rows from existing active Store Credit / Return Credit vouchers.
"""

import frappe
from frappe.utils import flt


def execute():
	if not frappe.db.exists("DocType", "Store Credit Wallet"):
		return

	rows = frappe.db.sql(
		"""
		SELECT issued_to AS customer, company, COALESCE(SUM(balance), 0) AS balance
		FROM `tabCH Voucher`
		WHERE voucher_type IN ('Store Credit', 'Return Credit')
		  AND status IN ('Active', 'Partially Used')
		  AND IFNULL(issued_to, '') != ''
		GROUP BY issued_to, company
		""",
		as_dict=True,
	)
	for row in rows:
		wallet_key = f"{row.company}::{row.customer}"
		name = frappe.db.get_value("Store Credit Wallet", {"wallet_key": wallet_key}, "name")
		if name:
			frappe.db.set_value("Store Credit Wallet", name, "current_balance", flt(row.balance), update_modified=False)
			continue
		doc = frappe.get_doc({
			"doctype": "Store Credit Wallet",
			"customer": row.customer,
			"company": row.company,
			"status": "Active",
			"current_balance": flt(row.balance),
		})
		doc.insert(ignore_permissions=True)
