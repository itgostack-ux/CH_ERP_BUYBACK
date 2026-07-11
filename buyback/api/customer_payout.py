"""
Buyback → Customer Payout via ch_payments
==========================================

Thin wrapper that translates a Buyback Order into a Bank Payment Request,
then hands off to the ch_payments provider framework.

No encryption, checksum, or bank-specific logic here — all of that lives
in ch_payments.bank_payments (BoBDigiNextProvider and friends).

Public API (whitelisted):
        - initiate_customer_payout(buyback_order)
        - get_payout_status(buyback_order)

Configuration:
        - Create one Bank Integration Profile per company with the desired
          bank endpoints (e.g. BoB Olive UAT/Prod). No code changes needed
          to switch banks — just change the profile.
"""

import frappe
from frappe import _


PAYOUT_MODE_BANK_TRANSFER = "Bank Transfer"


def _get_active_bank_profile(company: str):
	"""Return the active Bank Integration Profile for a company.

	Company-wise selection — one active profile per company. The profile
	itself decides which provider (BoB DigiNext, etc.) and which endpoints
	(DigiNext, Olive, prod, UAT) to use.
	"""
	profile_name = frappe.db.get_value(
		"Bank Integration Profile",
		{"company": company, "is_active": 1},
		"name",
	)
	if not profile_name:
		frappe.throw(
			_("No active Bank Integration Profile configured for company {0}.").format(company)
		)
	return frappe.get_doc("Bank Integration Profile", profile_name)


def _build_bank_payment_request(buyback_order, profile) -> "frappe.model.document.Document":
	"""Create (unsaved) Bank Payment Request from a Buyback Order."""
	bpr = frappe.new_doc("Bank Payment Request")
	bpr.bank_profile = profile.name
	bpr.company = buyback_order.company
	bpr.source_doctype = "Buyback Order"
	bpr.source_document = buyback_order.name
	bpr.party_type = "Customer"
	bpr.party = buyback_order.customer
	bpr.transaction_amount = buyback_order.final_price
	bpr.payment_mode = _select_payment_mode(buyback_order.final_price, profile)
	bpr.beneficiary_name = buyback_order.customer_bank_account_holder
	bpr.beneficiary_account_no = buyback_order.customer_bank_account_number
	bpr.beneficiary_ifsc = buyback_order.customer_bank_ifsc
	return bpr


def _select_payment_mode(amount, profile) -> str:
	"""Pick RTGS above the profile's threshold, otherwise the profile default."""
	rtgs_min = profile.rtgs_minimum_amount or 200000
	if amount and amount >= rtgs_min and "RTGS" in profile.get_supported_modes():
		return "RTGS"
	return profile.default_payment_mode or "NEFT"


def _validate_buyback_for_payout(buyback_order):
	"""Guard against creating a BPR for an order that isn't payout-ready."""
	if buyback_order.customer_payout_mode != PAYOUT_MODE_BANK_TRANSFER:
		frappe.throw(
			_("Customer payout mode must be {0}, got {1}.").format(
				PAYOUT_MODE_BANK_TRANSFER, buyback_order.customer_payout_mode
			)
		)
	missing = [
		label
		for label, val in (
			("Account Holder", buyback_order.customer_bank_account_holder),
			("Account Number", buyback_order.customer_bank_account_number),
			("IFSC", buyback_order.customer_bank_ifsc),
		)
		if not val
	]
	if missing:
		frappe.throw(_("Missing customer bank details: {0}").format(", ".join(missing)))
	if not buyback_order.final_price or buyback_order.final_price <= 0:
		frappe.throw(_("Buyback Order has no payable amount."))


@frappe.whitelist()
def initiate_customer_payout(buyback_order: str) -> dict:
	"""Create + submit a Bank Payment Request for the given Buyback Order.

	Returns the BPR name. Actual bank API call is made by the standard
	`send_to_bank` action on the BPR after approval (maker-checker enforced
	by ch_payments).
	"""
	bo = frappe.get_doc("Buyback Order", buyback_order)
	_validate_buyback_for_payout(bo)

	profile = _get_active_bank_profile(bo.company)
	bpr = _build_bank_payment_request(bo, profile)
	bpr.insert()
	return {"bank_payment_request": bpr.name, "status": bpr.payment_status}


@frappe.whitelist()
def get_payout_status(buyback_order: str) -> dict:
	"""Return the latest payment status for a Buyback Order, if any."""
	name = frappe.db.get_value(
		"Bank Payment Request",
		{"source_doctype": "Buyback Order", "source_document": buyback_order},
		"name",
		order_by="creation desc",
	)
	if not name:
		return {"status": None, "bank_payment_request": None}
	bpr = frappe.get_doc("Bank Payment Request", name)
	return {
		"bank_payment_request": bpr.name,
		"status": bpr.payment_status,
		"cms_ref": bpr.get("cms_ref"),
		"bank_ref": bpr.get("bank_ref"),
		"utr_number": bpr.get("utr_number"),
	}
