"""Phase 4 smoke test.

Run:
	bench --site erpnext.local execute buyback.tests.test_phase4_returns_closed_loop.run
"""

import frappe


def _must(label, ok, detail=""):
	prefix = "[PASS]" if ok else "[FAIL]"
	print(f"  {prefix} {label}{(' - ' + detail) if detail else ''}")
	if not ok:
		raise AssertionError(label)


def run():
	print("Phase 4 - Returns Closed Loop Smoke")
	for dt in ["Refurbishment Order", "Store Credit Wallet"]:
		_must(f"DocType exists: {dt}", bool(frappe.db.exists("DocType", dt)))

	customer = frappe.db.get_value("Customer", {}, "name")
	company = frappe.db.get_default("Company") or frappe.db.get_value("Company", {}, "name")
	_must("Customer available", bool(customer))
	_must("Company available", bool(company))

	from buyback.buyback.doctype.store_credit_wallet.store_credit_wallet import issue_wallet_credit
	credit = issue_wallet_credit(customer=customer, amount=123, company=company, reason="Phase 4 smoke")
	_must("Wallet credit created", bool(credit.get("wallet")), str(credit))
	_must("Voucher issued", bool(credit.get("voucher_name") or credit.get("voucher_code")), str(credit))

	item_code = frappe.db.get_value("Item", {"disabled": 0}, "name")
	_must("Item available", bool(item_code))
	order = frappe.get_doc({
		"doctype": "Refurbishment Order",
		"company": company,
		"customer": customer,
		"item_code": item_code,
		"qty": 1,
		"physical_condition": "Damaged",
		"status": "Received",
	})
	order.insert(ignore_permissions=True)
	_must("Refurb order inserted", bool(order.name), order.name)

	from buyback.buyback.report.refurb_pipeline.refurb_pipeline import execute
	cols, data, _, _, _ = execute({"company": company})
	_must("Refurb pipeline columns", any(c.get("fieldname") == "status" for c in cols))
	_must("Refurb pipeline data shape", isinstance(data, list))
	print("Phase 4 - ALL PASS")
