from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

from buyback.utils import assert_buyback_scope, is_privileged_user


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


def _require_wallet_permissions(customer: str, company: str, permission_type: str) -> None:
	customer_doc = frappe.get_doc("Customer", customer)
	company_doc = frappe.get_doc("Company", company)
	if is_privileged_user():
		return
	for doctype, ptype, doc in (
		("Customer", "read", customer_doc),
		("Company", "read", company_doc),
		("CH Voucher", "read", None),
		("Store Credit Wallet", permission_type, None),
	):
		if not frappe.has_permission(doctype, ptype=ptype, doc=doc):
			frappe.throw(
				_("You do not have {0} permission for {1}.").format(ptype, doctype),
				frappe.PermissionError,
			)
	assert_buyback_scope(company=company)


def get_or_create_wallet(customer: str, company: str) -> str:
	if not customer or not company:
		frappe.throw(_("Customer and Company are required."), frappe.ValidationError)
	wallet_name = frappe.db.get_value(
		"Store Credit Wallet",
		{"wallet_key": f"{company}::{customer}"},
		"name",
		for_update=True,
	)
	if wallet_name:
		_require_wallet_permissions(customer, company, "write")
		wallet = frappe.get_doc("Store Credit Wallet", wallet_name)
		wallet.check_permission("write")
		wallet.current_balance = _wallet_balance(customer, company)
		wallet.save()
		return wallet.name

	_require_wallet_permissions(customer, company, "create")
	wallet = frappe.get_doc({
		"doctype": "Store Credit Wallet",
		"customer": customer,
		"company": company,
		"status": "Active",
	})
	wallet.insert()
	return wallet.name


def issue_wallet_credit(customer: str, amount, company: str, pos_invoice: str | None = None, reason: str | None = None) -> dict:
	from ch_item_master.ch_item_master.voucher_api import issue_return_credit

	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Amount must be greater than zero"))
	if pos_invoice:
		invoice = frappe.get_doc("Sales Invoice", pos_invoice)
		invoice.check_permission("read")
		if invoice.customer != customer or invoice.company != company or not invoice.is_return:
			frappe.throw(
				_("The return invoice does not match the selected customer and company."),
				frappe.ValidationError,
			)

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
	wallet.check_permission("write")
	wallet.last_voucher = voucher_name
	wallet.last_credit_amount = amount
	wallet.last_credit_at = now_datetime()
	wallet.last_reference_invoice = pos_invoice
	wallet.current_balance = _wallet_balance(customer, company)
	wallet.save()
	return {
		"wallet": wallet.name,
		"balance": wallet.current_balance,
		"voucher_name": voucher_name,
		"voucher_code": voucher.get("voucher_code"),
		"voucher_type": voucher.get("voucher_type"),
	}
