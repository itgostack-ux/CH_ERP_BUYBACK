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


# Reason recorded for a device the national registry itself rejected. The other
# reasons on the Select are things a human asserts (Stolen, Police Complaint);
# this one states only what the CEIR portal returned, which is all the check
# actually tells us.
CEIR_REASON = "CEIR Flagged"

# Sanchar Saathi verdicts that mean the handset must never be bought — the same
# set both submit_imei_validation() methods treat as auto-reject outcomes.
CEIR_BLOCKING_STATUSES = ("Blacklisted", "Duplicate IMEI", "Already In Use")


def record_ceir_block(
	imei: str,
	ceir_status: str,
	*,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	remarks: str | None = None,
) -> str | None:
	"""Put an IMEI the Sanchar Saathi check rejected onto the buyback blacklist.

	Recording the verdict on the assessment or order alone only stops THAT
	document — the same handset could be walked into another store the next
	day and assessed again from scratch, because `check_imei_and_block` reads
	this doctype and nothing ever wrote to it. The blacklist is what makes the
	rejection stick bench-wide.

	Idempotent: `imei` is unique, so a repeat check updates (and re-activates)
	the existing row instead of failing the insert. Written with
	ignore_permissions because it is a compliance record produced by an
	already-authorised action — Buyback Agent has read-only access here by
	design, so agents cannot hand-write blacklist entries.

	Returns the blacklist docname, or None when there is nothing to record.
	"""
	imei = (imei or "").strip()
	if not imei or ceir_status not in CEIR_BLOCKING_STATUSES:
		return None

	note = _("Sanchar Saathi (CEIR) check returned {0}.").format(ceir_status)
	if reference_doctype and reference_name:
		note += " " + _("Recorded from {0} {1}.").format(reference_doctype, reference_name)
	if (remarks or "").strip():
		note += " " + remarks.strip()

	existing = frappe.db.get_value("Buyback IMEI Blacklist", {"imei": imei}, "name")
	if existing:
		doc = frappe.get_doc("Buyback IMEI Blacklist", existing)
		doc.active = 1
		doc.remarks = note
		doc.save(ignore_permissions=True)
		return doc.name

	doc = frappe.get_doc(
		{
			"doctype": "Buyback IMEI Blacklist",
			"imei": imei,
			"reason": CEIR_REASON,
			"active": 1,
			"reported_date": frappe.utils.nowdate(),
			"reported_by": frappe.session.user,
			"remarks": note,
			"reference_number": reference_name or "",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name
