# Copyright (c) 2026, GoStack and contributors
# Buyback SLA Engine — Tracks, evaluates, and flags SLA breaches

"""
SLA tracking architecture:
- Custom fields added to Buyback Order and Exchange Order for SLA tracking
- Scheduled job evaluates all open transactions every 5 minutes
- SLA status: On Time / Warning / Breach
- Warning = 80% of target elapsed
"""

import frappe
from frappe import _
from frappe.utils import (
    now_datetime, get_datetime, time_diff_in_seconds, cint,
    add_to_date, nowdate, get_url_to_form,
)

# ─── Default SLA targets (minutes) ──────────────────────────────────────────
DEFAULT_SLAS = {
    "quote_to_inspection": 30,
    "inspection_to_confirmation": 10,
    "confirmation_to_approval": 60,
    "approval_to_payment": 15,
    "payment_to_stock_entry": 30,
    "exchange_delivery_to_pickup": 2880,  # 48 hours
    "variance_approval": 20,
}


def get_sla_settings():
    """Return SLA settings with defaults."""
    try:
        settings = frappe.get_single("Buyback SLA Settings")
        rules = {}
        for rule in (settings.sla_rules or []):
            if rule.is_active:
                rules[rule.sla_name] = {
                    "target_minutes": cint(rule.target_minutes),
                    "applies_to": rule.applies_to_doctype,
                    "start_field": rule.start_field,
                    "end_field": rule.end_field,
                }
        return settings, rules
    except Exception:
        return None, {}


def calculate_sla_status(start_time, end_time, target_minutes):
    """Calculate SLA status given timestamps and target.

    Returns:
        dict with keys: status, minutes_taken, due_time
    """
    if not start_time:
        return {"status": "", "minutes_taken": 0, "due_time": None}

    start = get_datetime(start_time)
    due = add_to_date(start, minutes=target_minutes)

    if end_time:
        # Completed — check if on time
        end = get_datetime(end_time)
        minutes_taken = round(time_diff_in_seconds(end, start) / 60, 1)
        status = "On Time" if minutes_taken <= target_minutes else "Breach"
    else:
        # Still open — check current position
        now = now_datetime()
        minutes_taken = round(time_diff_in_seconds(now, start) / 60, 1)
        if minutes_taken > target_minutes:
            status = "Breach"
        elif minutes_taken > target_minutes * 0.8:
            status = "Warning"
        else:
            status = "On Time"

    return {
        "status": status,
        "minutes_taken": minutes_taken,
        "due_time": str(due),
    }


# ─── Scheduled job: evaluate all open SLAs ───────────────────────────────────

def evaluate_all_slas():
    """Scheduled job — runs every 5 minutes.
    Evaluates SLA for all in-progress transactions.
    """
    _evaluate_order_slas()
    _evaluate_exchange_slas()
    _evaluate_inspection_slas()


def _evaluate_order_slas():
    """Evaluate SLAs on open Buyback Orders."""
    open_orders = frappe.get_all(
        "Buyback Order",
        filters={"docstatus": ["<", 2], "status": ["not in", ["Closed", "Cancelled", "Rejected"]]},
        fields=["name", "status", "creation", "approval_date",
                "otp_verified_at", "modified"],
        limit=500,
    )

    for order in open_orders:
        sla_data = {}

        # SLA 1: Approval → Payment (applies when status = Approved or later)
        if order.status in ("Approved", "Awaiting OTP", "OTP Verified", "Ready to Pay"):
            sla = calculate_sla_status(
                order.approval_date or order.creation,
                None,
                DEFAULT_SLAS["approval_to_payment"]
            )
            _create_sla_log(
                "Buyback Order", order.name, "approval_to_payment",
                sla["minutes_taken"], breached=(sla["status"] == "Breach"),
                start_time=order.approval_date or order.creation,
                expected_minutes=DEFAULT_SLAS["approval_to_payment"],
            )
            if sla["status"] == "Breach":
                _fire_sla_alert("Buyback Order", order.name,
                                "approval_to_payment", sla["minutes_taken"])

        # SLA 2: Order creation → OTP/Payment completion
        if order.status == "Awaiting Approval":
            # Pending approval — check variance approval SLA
            sla = calculate_sla_status(
                order.creation,
                order.approval_date,
                DEFAULT_SLAS["variance_approval"]
            )
            _create_sla_log(
                "Buyback Order", order.name, "variance_approval",
                sla["minutes_taken"], breached=(sla["status"] == "Breach"),
                start_time=order.creation, end_time=order.approval_date,
                expected_minutes=DEFAULT_SLAS["variance_approval"],
            )
            if sla["status"] == "Breach":
                _fire_sla_alert("Buyback Order", order.name,
                                "variance_approval", sla["minutes_taken"])


def _evaluate_exchange_slas():
    """Evaluate SLAs on open Exchange Orders."""
    open_exchanges = frappe.get_all(
        "Buyback Exchange Order",
        filters={"docstatus": ["<", 2], "status": ["not in", ["Closed", "Cancelled"]]},
        fields=["name", "status", "new_device_delivered_at",
                "old_device_received_at", "creation"],
        limit=500,
    )

    for ex in open_exchanges:
        if ex.status in ("New Device Delivered", "Awaiting Pickup") and ex.new_device_delivered_at:
            sla = calculate_sla_status(
                ex.new_device_delivered_at,
                ex.old_device_received_at,
                DEFAULT_SLAS["exchange_delivery_to_pickup"]
            )
            _create_sla_log(
                "Buyback Exchange Order", ex.name, "exchange_delivery_to_pickup",
                sla["minutes_taken"], breached=(sla["status"] == "Breach"),
                start_time=ex.new_device_delivered_at, end_time=ex.old_device_received_at,
                expected_minutes=DEFAULT_SLAS["exchange_delivery_to_pickup"],
            )
            if sla["status"] == "Breach":
                _fire_sla_alert("Buyback Exchange Order", ex.name,
                                "exchange_delivery_to_pickup", sla["minutes_taken"])


def _evaluate_inspection_slas():
    """Evaluate SLAs on open Inspections."""
    open_inspections = frappe.get_all(
        "Buyback Inspection",
        filters={"status": "In Progress"},
        fields=["name", "inspection_started_at", "inspection_completed_at"],
        limit=500,
    )

    for insp in open_inspections:
        if insp.inspection_started_at:
            sla = calculate_sla_status(
                insp.inspection_started_at,
                insp.inspection_completed_at,
                DEFAULT_SLAS["quote_to_inspection"]
            )
            _create_sla_log(
                "Buyback Inspection", insp.name, "inspection_delay",
                sla["minutes_taken"], breached=(sla["status"] == "Breach"),
                start_time=insp.inspection_started_at,
                end_time=insp.inspection_completed_at,
                expected_minutes=DEFAULT_SLAS["quote_to_inspection"],
            )
            if sla["status"] == "Breach":
                _fire_sla_alert("Buyback Inspection", insp.name,
                                "inspection_delay", sla["minutes_taken"])


def _fire_sla_alert(doctype, name, sla_type, minutes_taken):
    """Create an alert for SLA breach."""
    alert_key = f"sla_breach_{doctype}_{name}_{sla_type}"

    # Avoid duplicate alerts in same hour
    if frappe.cache.get_value(alert_key):
        return

    doc = frappe.get_doc(doctype, name)
    store = getattr(doc, "store", "")
    url = get_url_to_form(doctype, name)

    message = (
        f"⚠️ SLA Breach: {sla_type.replace('_', ' ').title()} — "
        f"{doctype} {name} at store {store}. "
        f"Time elapsed: {minutes_taken:.0f} min. "
        f"<a href='{url}'>View</a>"
    )

    # ERPNext notification
    try:
        frappe.publish_realtime(
            "msgprint",
            {"message": message, "indicator": "red"},
            user=doc.owner,
        )
    except Exception:
        pass

    # Log to audit
    try:
        _log_sla_breach(doctype, name, sla_type, minutes_taken)
    except Exception:
        pass

    # Set cache to prevent re-alert for 1 hour
    frappe.cache.set_value(alert_key, 1, expires_in_sec=3600)


def _log_sla_breach(doctype, name, sla_type, minutes_taken):
    """Log SLA breach to Buyback Audit Log and Buyback SLA Log."""
    if frappe.db.exists("DocType", "Buyback Audit Log"):
        frappe.get_doc({
            "doctype": "Buyback Audit Log",
            "action": "SLA Breach" if "SLA Breach" in (
                frappe.get_meta("Buyback Audit Log").get_field("action").options or ""
            ) else "Order Created",
            "reference_doctype": doctype,
            "reference_name": name,
            "reason": f"SLA Breach: {sla_type} — {minutes_taken:.0f} minutes elapsed",
        }).insert(ignore_permissions=True)

    _create_sla_log(doctype, name, sla_type, minutes_taken, breached=True)


def _create_sla_log(doctype, name, sla_type, actual_minutes, breached=False,
                    start_time=None, end_time=None, expected_minutes=None):
    """Create a Buyback SLA Log record for tracking and reporting."""
    if not frappe.db.exists("DocType", "Buyback SLA Log"):
        return

    target = expected_minutes or DEFAULT_SLAS.get(sla_type, 0)

    # Avoid duplicate log for this doc+stage in the same evaluation window
    cache_key = f"sla_log_{doctype}_{name}_{sla_type}"
    if frappe.cache.get_value(cache_key):
        return
    frappe.cache.set_value(cache_key, 1, expires_in_sec=300)  # 5-min window

    try:
        doc = frappe.get_doc(doctype, name)
        store = getattr(doc, "store", None)
        company = getattr(doc, "company", None)
    except Exception:
        store = None
        company = None

    frappe.get_doc({
        "doctype": "Buyback SLA Log",
        "sla_stage": sla_type,
        "reference_doctype": doctype,
        "reference_name": name,
        "store": store,
        "company": company,
        "start_time": start_time,
        "end_time": end_time,
        "expected_minutes": target,
        "actual_minutes": round(actual_minutes, 1) if actual_minutes else 0,
        "breached": cint(breached),
        "status": "Breach" if breached else "On Time",
    }).insert(ignore_permissions=True)


# ─── SLA computation for a single document ───────────────────────────────────

@frappe.whitelist()
def get_order_sla_summary(order_name):
    """Get SLA status for all stages of a Buyback Order."""
    doc = frappe.get_doc("Buyback Order", order_name)

    # Assessment → Inspection (if linked)
    assessment_to_inspection = {}
    if doc.buyback_assessment and doc.buyback_inspection:
        a = frappe.get_doc("Buyback Assessment", doc.buyback_assessment)
        i = frappe.get_doc("Buyback Inspection", doc.buyback_inspection)
        assessment_to_inspection = calculate_sla_status(
            a.creation, i.creation,
            DEFAULT_SLAS["quote_to_inspection"]
        )
        assessment_to_inspection["label"] = "Assessment → Inspection"

    # Inspection → Confirmation
    insp_to_confirm = {}
    if doc.buyback_inspection:
        i = frappe.get_doc("Buyback Inspection", doc.buyback_inspection)
        insp_to_confirm = calculate_sla_status(
            i.inspection_completed_at, doc.creation,
            DEFAULT_SLAS["inspection_to_confirmation"]
        )
        insp_to_confirm["label"] = "Inspection → Order Created"

    # Order created → Approval
    order_to_approval = calculate_sla_status(
        doc.creation,
        doc.approval_date,
        DEFAULT_SLAS["confirmation_to_approval"]
    )
    order_to_approval["label"] = "Order → Approval"

    # Approval → Payment
    approval_to_payment = {}
    if doc.approval_date:
        # Find first payment timestamp
        first_payment = None
        for p in (doc.payments or []):
            if p.payment_date:
                if not first_payment or get_datetime(p.payment_date) < get_datetime(first_payment):
                    first_payment = p.payment_date
        approval_to_payment = calculate_sla_status(
            doc.approval_date,
            first_payment,
            DEFAULT_SLAS["approval_to_payment"]
        )
        approval_to_payment["label"] = "Approval → Payment"

    return {
        "quote_to_inspection": quote_to_inspection,
        "inspection_to_confirmation": insp_to_confirm,
        "order_to_approval": order_to_approval,
        "approval_to_payment": approval_to_payment,
    }


@frappe.whitelist()
def get_branch_sla_summary(store, date=None):
    """Get SLA summary for a branch on a given date."""
    date = date or nowdate()

    orders = frappe.get_all(
        "Buyback Order",
        filters={
            "store": store,
            "creation": (">=", f"{date} 00:00:00"),
            "docstatus": ["<", 2],
        },
        fields=["name", "status", "creation", "approval_date", "otp_verified_at"],
    )

    total = len(orders)
    breaches = 0
    warnings = 0
    on_time = 0

    for order in orders:
        if order.approval_date:
            sla = calculate_sla_status(
                order.creation, order.approval_date,
                DEFAULT_SLAS["confirmation_to_approval"]
            )
        else:
            sla = calculate_sla_status(
                order.creation, None,
                DEFAULT_SLAS["confirmation_to_approval"]
            )

        if sla["status"] == "Breach":
            breaches += 1
        elif sla["status"] == "Warning":
            warnings += 1
        else:
            on_time += 1

    return {
        "store": store,
        "date": date,
        "total_orders": total,
        "on_time": on_time,
        "warnings": warnings,
        "breaches": breaches,
        "compliance_pct": round(on_time / total * 100, 1) if total else 100,
    }
