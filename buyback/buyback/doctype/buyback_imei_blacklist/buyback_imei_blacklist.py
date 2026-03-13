import frappe
from frappe import _
from frappe.model.document import Document


class BuybackIMEIBlacklist(Document):
	pass


def is_imei_blacklisted(imei: str) -> dict | None:
	"""Check if an IMEI is on the active blacklist.

	Returns dict with reason and name if blacklisted, else None.
	"""
	if not imei:
		return None

	entry = frappe.db.get_value(
		"Buyback IMEI Blacklist",
		{"imei": imei, "active": 1},
		["name", "reason", "remarks", "reference_number"],
		as_dict=True,
	)
	return entry or None


def check_imei_and_block(imei: str):
	"""Raise ValidationError if IMEI is blacklisted. Call from validate()."""
	entry = is_imei_blacklisted(imei)
	if entry:
		frappe.throw(
			_("IMEI/Serial {0} is blacklisted — Reason: {1}.{2}").format(
				frappe.bold(imei),
				frappe.bold(entry.reason),
				f" Ref: {entry.reference_number}" if entry.reference_number else "",
			),
			title=_("Blacklisted Device"),
		)
