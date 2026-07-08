"""Phase B — Buyback lifecycle APIs.

Whitelisted entry points for the market-standard controls added in Phase B:

- **Indemnity / NOC capture** — record the customer's signed declaration.
- **Pickup appointment flow** — schedule, complete, fail, reschedule,
  auto-cap at three attempts.
- **Data-wipe certificate** — one-call creation from Buyback Order + evidence.

All APIs are permission-checked against the underlying Buyback Order.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

MAX_PICKUP_ATTEMPTS = 3


# ── Indemnity / NOC ──────────────────────────────────────────────────────


@frappe.whitelist()
def record_indemnity(
    order_name: str,
    signed_by_name: str,
    signature_type: str,
    attachment: str | None = None,
    notes: str | None = None,
) -> dict:
    """Record a customer NOC / indemnity against a Buyback Order.

    Market-standard gate (Cashify, Samsung Exchange, Best Buy Trade-In):
    a device cannot progress to Paid without a signed indemnity confirming
    the seller is the legal owner and consents to transfer.
    """
    frappe.has_permission("Buyback Order", ptype="write", throw=True)

    order = frappe.get_doc("Buyback Order", order_name)
    signed_by_name = (signed_by_name or "").strip()
    signature_type = (signature_type or "").strip()
    if not signed_by_name:
        frappe.throw(_("Customer name is required on the signed indemnity."))
    if signature_type not in {
        "E-Signature (Kiosk)",
        "Wet Signature Scanned",
        "Aadhaar OTP Consent",
        "Digilocker eSign",
    }:
        frappe.throw(_("Choose a valid signature type."))

    if order.docstatus == 2:
        frappe.throw(_("Cannot capture indemnity on a cancelled Buyback Order."))

    payload = {
        "indemnity_signed": 1,
        "indemnity_signed_at": now_datetime(),
        "indemnity_signature_type": signature_type,
        "indemnity_signed_by_name": signed_by_name,
        "indemnity_captured_by": frappe.session.user,
    }
    if attachment:
        payload["indemnity_attachment"] = attachment

    frappe.db.set_value(
        "Buyback Order", order_name, payload, update_modified=False
    )
    try:
        from buyback.utils import log_audit

        log_audit(
            "Indemnity Captured",
            "Buyback Order",
            order_name,
            new_value={
                "signed_by": signed_by_name,
                "type": signature_type,
                "attachment": bool(attachment),
                "notes": (notes or "")[:200],
            },
        )
    except Exception:
        # audit is best-effort — never block on it
        pass
    return {"order": order_name, **payload}


# ── Pickup lifecycle ─────────────────────────────────────────────────────


def _get_attempt_count(order_name: str) -> int:
    return frappe.db.count(
        "CH Buyback Pickup Appointment",
        filters={"buyback_order": order_name, "docstatus": ["<", 2]},
    )


@frappe.whitelist()
def schedule_pickup(
    order_name: str,
    appointment_date: str,
    appointment_slot: str | None = None,
    pickup_address: str | None = None,
    contact_phone: str | None = None,
    landmark: str | None = None,
    pincode: str | None = None,
    assigned_to: str | None = None,
    vendor_partner: str | None = None,
    vendor_reference: str | None = None,
    remarks: str | None = None,
) -> dict:
    """Create a new Buyback Pickup Appointment for an order.

    Auto-increments the attempt number; refuses to schedule beyond
    MAX_PICKUP_ATTEMPTS (raises an exception request instead).
    """
    frappe.has_permission("CH Buyback Pickup Appointment", ptype="create", throw=True)

    order = frappe.get_doc("Buyback Order", order_name)

    prior = _get_attempt_count(order_name)
    if prior >= MAX_PICKUP_ATTEMPTS:
        frappe.throw(
            _(
                "Buyback Order {0} has already reached the {1}-attempt cap. "
                "Escalate via CH Exception Request rather than scheduling "
                "another attempt."
            ).format(order_name, MAX_PICKUP_ATTEMPTS),
            title=_("Pickup Attempt Cap"),
        )

    if not pickup_address:
        # Fall back to customer's primary address if available.
        primary = frappe.db.get_value(
            "Customer", order.customer, "customer_primary_address"
        )
        if primary:
            addr = frappe.db.get_value(
                "Address",
                primary,
                ["address_line1", "address_line2", "city", "state", "pincode"],
                as_dict=True,
            )
            if addr:
                pickup_address = "\n".join(
                    [
                        (addr.get("address_line1") or "").strip(),
                        (addr.get("address_line2") or "").strip(),
                        ", ".join(
                            [
                                x
                                for x in [addr.get("city"), addr.get("state")]
                                if x
                            ]
                        ),
                    ]
                ).strip()
                if not pincode:
                    pincode = addr.get("pincode")

    doc = frappe.get_doc(
        {
            "doctype": "CH Buyback Pickup Appointment",
            "buyback_order": order_name,
            "customer": order.customer,
            "appointment_date": appointment_date,
            "appointment_slot": (appointment_slot or "").strip() or None,
            "pickup_address": pickup_address or "",
            "contact_phone": (contact_phone or order.mobile_no or "").strip() or None,
            "landmark": (landmark or "").strip() or None,
            "pincode": (pincode or "").strip() or None,
            "assigned_to": (assigned_to or "").strip() or None,
            "vendor_partner": (vendor_partner or "").strip() or None,
            "vendor_reference": (vendor_reference or "").strip() or None,
            "remarks": (remarks or "").strip() or None,
            "status": "Scheduled",
        }
    )
    doc.insert()
    doc.submit()
    return {"name": doc.name, "attempt_number": doc.attempt_number}


@frappe.whitelist()
def complete_pickup(appointment: str, remarks: str | None = None) -> dict:
    frappe.has_permission("CH Buyback Pickup Appointment", ptype="write", throw=True)
    doc = frappe.get_doc("CH Buyback Pickup Appointment", appointment)
    if doc.docstatus != 1:
        frappe.throw(_("Pickup Appointment must be Submitted before it can be completed."))
    if doc.status == "Completed":
        return {"name": doc.name, "status": doc.status}

    doc.db_set("status", "Completed")
    doc.db_set("completed_at", now_datetime())
    if remarks:
        combined = (doc.remarks or "") + ("\n" if doc.remarks else "") + remarks
        doc.db_set("remarks", combined)
    # Re-stamp the parent Buyback Order.
    doc.reload()
    doc._stamp_buyback_order()
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def fail_pickup(
    appointment: str,
    failure_reason: str,
    next_action: str,
    remarks: str | None = None,
) -> dict:
    frappe.has_permission("CH Buyback Pickup Appointment", ptype="write", throw=True)
    if not failure_reason:
        frappe.throw(_("Failure Reason is required."))
    if not next_action:
        frappe.throw(_("Next Action is required."))

    doc = frappe.get_doc("CH Buyback Pickup Appointment", appointment)
    if doc.docstatus != 1:
        frappe.throw(_("Pickup Appointment must be Submitted before failure can be recorded."))
    if doc.status in ("Completed", "Cancelled"):
        frappe.throw(_("Cannot mark a {0} attempt as Failed.").format(doc.status))

    doc.db_set("status", "Attempted (Failed)")
    doc.db_set("failure_reason", failure_reason)
    doc.db_set("next_action", next_action)
    if remarks:
        combined = (doc.remarks or "") + ("\n" if doc.remarks else "") + remarks
        doc.db_set("remarks", combined)

    # Auto-escalate when the third attempt has failed.
    if doc.attempt_number >= MAX_PICKUP_ATTEMPTS or next_action == "Cancel Order":
        _raise_pickup_exhaustion_exception(doc)

    return {"name": doc.name, "status": doc.status, "attempt_number": doc.attempt_number}


@frappe.whitelist()
def reschedule_pickup(
    appointment: str,
    appointment_date: str,
    appointment_slot: str | None = None,
) -> dict:
    """Create a follow-up appointment linked to the failed prior attempt."""
    frappe.has_permission("CH Buyback Pickup Appointment", ptype="create", throw=True)

    prior = frappe.get_doc("CH Buyback Pickup Appointment", appointment)
    if prior.status != "Attempted (Failed)":
        frappe.throw(_("Only failed attempts can be rescheduled."))
    if prior.reschedule_to:
        frappe.throw(
            _("This attempt has already been rescheduled to {0}.").format(
                prior.reschedule_to
            )
        )

    order_name = prior.buyback_order
    prior_count = _get_attempt_count(order_name)
    if prior_count >= MAX_PICKUP_ATTEMPTS:
        frappe.throw(
            _(
                "Buyback Order {0} has already reached the {1}-attempt cap."
            ).format(order_name, MAX_PICKUP_ATTEMPTS),
            title=_("Pickup Attempt Cap"),
        )

    new_doc = schedule_pickup(
        order_name=order_name,
        appointment_date=appointment_date,
        appointment_slot=appointment_slot,
        pickup_address=prior.pickup_address,
        contact_phone=prior.contact_phone,
        landmark=prior.landmark,
        pincode=prior.pincode,
        assigned_to=prior.assigned_to,
        vendor_partner=prior.vendor_partner,
    )
    frappe.db.set_value(
        "CH Buyback Pickup Appointment",
        appointment,
        {"status": "Rescheduled", "reschedule_to": new_doc["name"]},
        update_modified=False,
    )
    return new_doc


def _raise_pickup_exhaustion_exception(doc):
    """When the customer misses 3 attempts, log a CH Exception Request."""
    if not frappe.db.exists("DocType", "CH Exception Request"):
        return
    try:
        from ch_item_master.ch_item_master.exception_api import raise_exception

        if not frappe.db.exists("CH Exception Type", "Buyback Pickup Exhausted"):
            return
        raise_exception(
            exception_type="Buyback Pickup Exhausted",
            company=frappe.db.get_value("Buyback Order", doc.buyback_order, "company"),
            reason=(
                f"Pickup attempts exhausted after {doc.attempt_number} tries. "
                f"Last failure: {doc.failure_reason}. Next action: {doc.next_action}."
            ),
            reference_doctype="Buyback Order",
            reference_name=doc.buyback_order,
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            title=f"Pickup exhaustion exception failed for {doc.name}",
        )


# ── Data-Wipe Certificate ────────────────────────────────────────────────


@frappe.whitelist()
def record_data_wipe(
    order_name: str,
    wipe_method: str,
    wipe_standard: str | None = None,
    wipe_tool: str | None = None,
    wipe_duration_minutes: int | None = None,
    evidence_attachment: str | None = None,
    evidence_screenshot: str | None = None,
    remarks: str | None = None,
    submit: bool = True,
) -> dict:
    """Create a Data Wipe Certificate for a Buyback Order and (by default) submit it."""
    frappe.has_permission("CH Data Wipe Certificate", ptype="create", throw=True)

    order = frappe.get_doc("Buyback Order", order_name)

    if not wipe_method:
        frappe.throw(_("Wipe Method is required."))

    # Try to resolve a matching Serial No when the buyback captured an IMEI.
    serial_no_name = None
    if order.imei_serial:
        serial_no_name = frappe.db.get_value(
            "Serial No",
            {"serial_no": order.imei_serial},
            "name",
        )

    doc = frappe.get_doc(
        {
            "doctype": "CH Data Wipe Certificate",
            "buyback_order": order_name,
            "customer": order.customer,
            "item": order.item,
            "imei_serial": order.imei_serial,
            "brand": getattr(order, "brand", None),
            "serial_no": serial_no_name,
            "wipe_method": wipe_method,
            "wipe_standard": (wipe_standard or "").strip() or None,
            "wipe_tool": (wipe_tool or "").strip() or None,
            "wipe_duration_minutes": cint(wipe_duration_minutes) or None,
            "wiped_by": frappe.session.user,
            "wiped_at": now_datetime(),
            "evidence_attachment": (evidence_attachment or "").strip() or None,
            "evidence_screenshot": (evidence_screenshot or "").strip() or None,
            "remarks": (remarks or "").strip() or None,
            "status": "Draft",
        }
    )
    doc.insert()
    if cint(submit):
        doc.submit()
    return {
        "name": doc.name,
        "status": doc.status,
        "buyback_order": doc.buyback_order,
    }


@frappe.whitelist()
def verify_data_wipe(certificate: str, verification_method: str | None = None) -> dict:
    """Second-person verification of a submitted wipe certificate.

    Enforces the maker-checker rule (verifier != wiper) at validate time
    via CHDataWipeCertificate.validate.
    """
    frappe.has_permission("CH Data Wipe Certificate", ptype="write", throw=True)
    doc = frappe.get_doc("CH Data Wipe Certificate", certificate)
    if doc.docstatus != 1:
        frappe.throw(_("Certificate must be Submitted before it can be verified."))
    if doc.wiped_by == frappe.session.user:
        frappe.throw(
            _("You cannot verify a wipe you performed (maker-checker rule)."),
            title=_("Maker-Checker Required"),
        )

    doc.db_set("wipe_verified", 1)
    doc.db_set("verified_by", frappe.session.user)
    doc.db_set("verified_at", now_datetime())
    if verification_method:
        doc.db_set("verification_method", verification_method)
    doc.db_set("status", "Verified")
    return {"name": doc.name, "status": "Verified"}
