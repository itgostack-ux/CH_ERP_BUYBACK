from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime


def _wallet_balance(customer: str, company: str) -> float:
	rows = frappe.get_all(
		"CH Voucher",
		filters={
			"issued_to": customer,
			"company": company,
			"voucher_type": ["in", ["Store Credit", "Return Credit"]],
			"status": ["in", ["Active", "Partially Used"]],
		},
		fields=["balance"],
	)
	return flt(sum(flt(r.balance) for r in rows))


class StoreCreditWallet(Document):
	def validate(self):
		if not self.customer or not self.company:
			frappe.throw(_("Customer and Company are required"))
		self.wallet_key = f"{self.company}::{self.customer}"
		self.current_balance = _wallet_balance(self.customer, self.company)
		if not self.status:
			self.status = "Active"


@frappe.whitelist()
def get_or_create_wallet(customer: str, company: str) -> str:
	wallet_name = frappe.db.get_value(
		"Store Credit Wallet",
		{"wallet_key": f"{company}::{customer}"},
		"name",
	)
	if wallet_name:
		wallet = frappe.get_doc("Store Credit Wallet", wallet_name)
		wallet.current_balance = _wallet_balance(customer, company)
		wallet.save(ignore_permissions=True)
		return wallet.name

	wallet = frappe.get_doc({
		"doctype": "Store Credit Wallet",
		"customer": customer,
		"company": company,
		"status": "Active",
	})
	wallet.insert(ignore_permissions=True)
	return wallet.name


@frappe.whitelist()
def issue_wallet_credit(customer: str, amount, company: str, pos_invoice: str | None = None, reason: str | None = None) -> dict:
	from ch_item_master.ch_item_master.voucher_api import issue_return_credit

	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Amount must be greater than zero"))

	voucher = issue_return_credit(
		customer=customer,
		amount=amount,
		company=company,
		pos_invoice=pos_invoice,
		reason=reason,
	)
	voucher_name = voucher.get("voucher_name") or voucher.get("name")
	if not voucher_name and voucher.get("voucher_code"):
		voucher_name = frappe.db.get_value("CH Voucher", {"voucher_code": voucher.get("voucher_code")}, "name")
	wallet_name = get_or_create_wallet(customer, company)
	wallet = frappe.get_doc("Store Credit Wallet", wallet_name)
	wallet.last_voucher = voucher_name
	wallet.last_credit_amount = amount
	wallet.last_credit_at = now_datetime()
	wallet.last_reference_invoice = pos_invoice
	wallet.current_balance = _wallet_balance(customer, company)
	wallet.save(ignore_permissions=True)
	return {
		"wallet": wallet.name,
		"balance": wallet.current_balance,
		"voucher_name": voucher_name,
		"voucher_code": voucher.get("voucher_code"),
		"voucher_type": voucher.get("voucher_type"),
	}
