# Copyright (c) 2026, GoStack and contributors
# Buyback SLA Engine — Tracks, evaluates, and flags SLA breaches

"""
SLA tracking architecture:
- Custom fields added to Buyback Order and Exchange Order for SLA tracking
- Scheduled job evaluates all open transactions every 5 minutes
- SLA status: On Time / Warning / Breach
- Warning = 80% of target elapsed
"""

import hashlib

import frappe
from frappe import _
from frappe.utils import (
    now_datetime, get_datetime, time_diff_in_seconds, cint,
    add_days, add_to_date, getdate, nowdate, get_url_to_form,
)

from buyback.utils import (
    assert_buyback_scope,
    claim_scheduler_alert,
    get_int_setting,
    is_privileged_user,
    new_scheduler_alert_budget,
    require_configured_role,
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


def _sla_target(sla_name):
    _settings, rules = get_sla_settings()
    configured = cint((rules.get(sla_name) or {}).get("target_minutes"))
    return configured or DEFAULT_SLAS[sla_name]


def _configured_sla_targets():
    _settings, rules = get_sla_settings()
    return {
        name: cint((rules.get(name) or {}).get("target_minutes")) or default
        for name, default in DEFAULT_SLAS.items()
    }


def _rotating_sla_rows(doctype, filters, fields, cache_key, batch_limit):
    cursor = frappe.cache.get_value(cache_key)
    if isinstance(cursor, bytes):
        cursor = cursor.decode()

    def fetch(name_filter, limit):
        row_filters = dict(filters)
        if name_filter:
            row_filters["name"] = name_filter
        return frappe.get_all(
            doctype,
            filters=row_filters,
            fields=fields,
            order_by="name asc",
            limit_page_length=limit,
        )

    rows = fetch((">", cursor) if cursor else None, batch_limit)
    if cursor and len(rows) < batch_limit:
        rows.extend(fetch(("<=", cursor), batch_limit - len(rows)))
    if rows:
        frappe.cache.set_value(cache_key, rows[-1].name, expires_in_sec=7 * 86400)
    return rows


def _require_sla_read(doctype, doc=None):
    if not is_privileged_user() and not frappe.has_permission(doctype, ptype="read", doc=doc):
        frappe.throw(
            _("You do not have read permission for {0}.").format(doctype),
            frappe.PermissionError,
        )


def _scoped_linked_doc(doctype, name, order):
    linked = frappe.get_doc(doctype, name)
    _require_sla_read(doctype, linked)
    if linked.get("company") and linked.company != order.company:
        frappe.throw(_("The linked SLA document has an invalid company."), frappe.ValidationError)
    if linked.get("store") and order.store and linked.store != order.store:
        frappe.throw(_("The linked SLA document has an invalid store."), frappe.ValidationError)
    assert_buyback_scope(store=linked.get("store"), company=linked.get("company"))
    return linked


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
    except frappe.DoesNotExistError:
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
    targets = _configured_sla_targets()
    alert_budget = new_scheduler_alert_budget()
    _evaluate_order_slas(targets, alert_budget)
    _evaluate_exchange_slas(targets, alert_budget)
    _evaluate_inspection_slas(targets, alert_budget)


def _evaluate_order_slas(targets=None, alert_budget=None):
    """Evaluate SLAs on open Buyback Orders."""
    targets = targets or _configured_sla_targets()
    batch_limit = min(get_int_setting("scheduler_batch_limit", 500), 5000)
    open_orders = _rotating_sla_rows(
        "Buyback Order",
        {"docstatus": ["<", 2], "status": ["not in", ["Closed", "Cancelled", "Rejected"]]},
        ["name", "status", "creation", "approval_date", "otp_verified_at", "store", "company"],
        "buyback_sla_order_cursor",
        batch_limit,
    )

    for order in open_orders:
        # SLA 1: Approval → Payment (applies when status = Approved or later)
        if order.status in ("Approved", "Awaiting OTP", "OTP Verified", "Ready to Pay"):
            target = targets["approval_to_payment"]
            sla = calculate_sla_status(
                order.approval_date or order.creation,
                None,
                target,
            )
            _create_sla_log(
                "Buyback Order", order.name, "approval_to_payment",
                sla["minutes_taken"], breached=(sla["status"] == "Breach"),
                start_time=order.approval_date or order.creation,
                expected_minutes=target, store=order.store, company=order.company,
                status=sla["status"],
            )
            if sla["status"] == "Breach":
                _fire_sla_alert("Buyback Order", order.name,
                                "approval_to_payment", sla["minutes_taken"], target, alert_budget)

        # SLA 2: Order creation → OTP/Payment completion
        if order.status == "Awaiting Approval":
            target = targets["variance_approval"]
            sla = calculate_sla_status(
                order.creation,
                order.approval_date,
                target,
            )
            _create_sla_log(
                "Buyback Order", order.name, "variance_approval",
                sla["minutes_taken"], breached=(sla["status"] == "Breach"),
                start_time=order.creation, end_time=order.approval_date,
                expected_minutes=target, store=order.store, company=order.company,
                status=sla["status"],
            )
            if sla["status"] == "Breach":
                _fire_sla_alert("Buyback Order", order.name,
                                "variance_approval", sla["minutes_taken"], target, alert_budget)


def _evaluate_exchange_slas(targets=None, alert_budget=None):
    """Evaluate SLAs on open Exchange Orders."""
    targets = targets or _configured_sla_targets()
    batch_limit = min(get_int_setting("scheduler_batch_limit", 500), 5000)
    open_exchanges = _rotating_sla_rows(
        "Buyback Exchange Order",
        {"docstatus": ["<", 2], "status": ["not in", ["Closed", "Cancelled"]]},
        ["name", "status", "new_device_delivered_at", "old_device_received_at", "creation", "store", "company"],
        "buyback_sla_exchange_cursor",
        batch_limit,
    )

    for ex in open_exchanges:
        if ex.status in ("New Device Delivered", "Awaiting Pickup") and ex.new_device_delivered_at:
            target = targets["exchange_delivery_to_pickup"]
            sla = calculate_sla_status(
                ex.new_device_delivered_at,
                ex.old_device_received_at,
                target,
            )
            _create_sla_log(
                "Buyback Exchange Order", ex.name, "exchange_delivery_to_pickup",
                sla["minutes_taken"], breached=(sla["status"] == "Breach"),
                start_time=ex.new_device_delivered_at, end_time=ex.old_device_received_at,
                expected_minutes=target, store=ex.store, company=ex.company,
                status=sla["status"],
            )
            if sla["status"] == "Breach":
                _fire_sla_alert("Buyback Exchange Order", ex.name,
                                "exchange_delivery_to_pickup", sla["minutes_taken"], target, alert_budget)


def _evaluate_inspection_slas(targets=None, alert_budget=None):
    """Evaluate SLAs on open Inspections."""
    targets = targets or _configured_sla_targets()
    batch_limit = min(get_int_setting("scheduler_batch_limit", 500), 5000)
    open_inspections = _rotating_sla_rows(
        "Buyback Inspection",
        {"status": "In Progress"},
        ["name", "inspection_started_at", "inspection_completed_at", "store", "company"],
        "buyback_sla_inspection_cursor",
        batch_limit,
    )

    for insp in open_inspections:
        if insp.inspection_started_at:
            target = targets["quote_to_inspection"]
            sla = calculate_sla_status(
                insp.inspection_started_at,
                insp.inspection_completed_at,
                target,
            )
            _create_sla_log(
                "Buyback Inspection", insp.name, "inspection_delay",
                sla["minutes_taken"], breached=(sla["status"] == "Breach"),
                start_time=insp.inspection_started_at,
                end_time=insp.inspection_completed_at,
                expected_minutes=target, store=insp.store, company=insp.company,
                status=sla["status"],
            )
            if sla["status"] == "Breach":
                _fire_sla_alert("Buyback Inspection", insp.name,
                                "inspection_delay", sla["minutes_taken"], target, alert_budget)


def _fire_sla_alert(doctype, name, sla_type, minutes_taken, target_minutes=None, alert_budget=None):
    """Create an alert for SLA breach.

    Notification scoping (was: broadcast to doc.owner, causing toast spam to
    whichever Cashier/Sales Exec first created the order — see Audit
    Finding 2026-06-19 "SLA toast pile-up"):

      → User-facing notification (in-app bell + realtime toast + optional
        WhatsApp) is delegated to `alerts.alert_sla_breach()`, which resolves
        recipients through `notification_router.get_scoped_users()` and
        filters by:
            - Roles: the configured Buyback Settings SLA alert role set.
            - Store: only users whose CH User Scope covers the order's
              store get the toast. Bypass users (National Head, COO, etc.)
              see all stores. Mirrors SAP S/4HANA plant-based notification
              scoping and Oracle NetSuite role+subsidiary targeting.
            - Doc owner is added as a follower (Odoo followers-model
              equivalent) so the original creator still gets the alert.

      → Audit logging (Buyback Audit Log + Buyback SLA Log) stays here
        — it is recipient-independent and must run on every breach.

      → 1-hour de-dup cache stays here.
    """
    alert_key = f"sla_breach_{doctype}_{name}_{sla_type}"

    # Avoid duplicate alerts in same hour
    if frappe.cache.get_value(alert_key):
        return
    if not claim_scheduler_alert(alert_budget):
        return

    # Always log the breach (audit), even if recipient resolution fails.
    try:
        _log_sla_breach(doctype, name, sla_type, minutes_taken)
    except Exception:
        frappe.log_error(title="SLA breach logging failed")

    # Dispatch user-facing notification through the scoped path.
    target_minutes = target_minutes or DEFAULT_SLAS.get(sla_type, 0)
    try:
        from buyback.buyback.alerts import alert_sla_breach
        alert_sla_breach(doctype, name, sla_type, minutes_taken, target_minutes)
    except Exception:
        frappe.log_error(title=f"SLA breach notification failed for {doctype} {name}")

    # Set cache to prevent re-alert for 1 hour
    frappe.cache.set_value(alert_key, 1, expires_in_sec=3600)


def _log_sla_breach(doctype, name, sla_type, minutes_taken):
    """Log one SLA breach audit record."""
    frappe.get_doc({
        "doctype": "Buyback Audit Log",
        "action": "SLA Breach" if "SLA Breach" in (
            frappe.get_meta("Buyback Audit Log").get_field("action").options or ""
        ) else "Order Created",
        "reference_doctype": doctype,
        "reference_name": name,
        "reason": f"SLA Breach: {sla_type} — {minutes_taken:.0f} minutes elapsed",
    }).insert(ignore_permissions=True)


def _create_sla_log(doctype, name, sla_type, actual_minutes, breached=False,
                    start_time=None, end_time=None, expected_minutes=None,
                    store=None, company=None, status=None):
    """Upsert the current SLA stage snapshot without growing one row per poll."""
    target = expected_minutes or DEFAULT_SLAS.get(sla_type, 0)
    actual = round(actual_minutes, 1) if actual_minutes else 0
    status = status or ("Breach" if breached else "On Time")
    breached = cint(status == "Breach" or breached)
    log_name = "SLA-" + hashlib.sha256(
        f"{doctype}\0{name}\0{sla_type}".encode()
    ).hexdigest()[:32]
    timestamp = now_datetime()
    user = frappe.session.user
    try:
        frappe.db.sql(
            """
            INSERT INTO `tabBuyback SLA Log`
                (name, creation, modified, owner, modified_by, docstatus, idx,
                 sla_stage, reference_doctype, reference_name, store, company,
                 start_time, end_time, expected_minutes, actual_minutes,
                 exceeded_by, breached, status)
            VALUES
                (%(name)s, %(timestamp)s, %(timestamp)s, %(user)s, %(user)s, 0, 0,
                 %(sla_stage)s, %(reference_doctype)s, %(reference_name)s, %(store)s, %(company)s,
                 %(start_time)s, %(end_time)s, %(expected_minutes)s, %(actual_minutes)s,
                 %(exceeded_by)s, %(breached)s, %(status)s)
            ON DUPLICATE KEY UPDATE
                modified = VALUES(modified),
                modified_by = VALUES(modified_by),
                store = VALUES(store),
                company = VALUES(company),
                start_time = COALESCE(VALUES(start_time), start_time),
                end_time = COALESCE(VALUES(end_time), end_time),
                expected_minutes = VALUES(expected_minutes),
                actual_minutes = VALUES(actual_minutes),
                exceeded_by = VALUES(exceeded_by),
                breached = VALUES(breached),
                status = VALUES(status)
            """,
            {
                "name": log_name,
                "timestamp": timestamp,
                "user": user,
                "sla_stage": sla_type,
                "reference_doctype": doctype,
                "reference_name": name,
                "store": store,
                "company": company,
                "start_time": start_time,
                "end_time": end_time,
                "expected_minutes": target,
                "actual_minutes": actual,
                "exceeded_by": round(actual - target, 1),
                "breached": breached,
                "status": status,
            },
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"SLA log creation failed for {doctype} {name}")


# ─── SLA computation for a single document ───────────────────────────────────

@frappe.whitelist()
def get_order_sla_summary(order_name) -> dict:
    """Get SLA status for all stages of a Buyback Order."""
    require_configured_role("dashboard_roles", action=_("view order SLA details"))
    doc = frappe.get_doc("Buyback Order", order_name)
    _require_sla_read("Buyback Order", doc)
    assert_buyback_scope(store=doc.store, company=doc.company)

    # Assessment → Inspection (if linked)
    assessment_to_inspection = {}
    if doc.buyback_assessment and doc.buyback_inspection:
        a = _scoped_linked_doc("Buyback Assessment", doc.buyback_assessment, doc)
        i = _scoped_linked_doc("Buyback Inspection", doc.buyback_inspection, doc)
        assessment_to_inspection = calculate_sla_status(
            a.creation, i.creation,
            _sla_target("quote_to_inspection")
        )
        assessment_to_inspection["label"] = "Assessment → Inspection"

    # Inspection → Confirmation
    insp_to_confirm = {}
    if doc.buyback_inspection:
        i = _scoped_linked_doc("Buyback Inspection", doc.buyback_inspection, doc)
        insp_to_confirm = calculate_sla_status(
            i.inspection_completed_at, doc.creation,
            _sla_target("inspection_to_confirmation")
        )
        insp_to_confirm["label"] = "Inspection → Order Created"

    # Order created → Approval
    order_to_approval = calculate_sla_status(
        doc.creation,
        doc.approval_date,
        _sla_target("confirmation_to_approval")
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
            _sla_target("approval_to_payment")
        )
        approval_to_payment["label"] = "Approval → Payment"

    return {
        "quote_to_inspection": assessment_to_inspection,
        "inspection_to_confirmation": insp_to_confirm,
        "order_to_approval": order_to_approval,
        "approval_to_payment": approval_to_payment,
    }


@frappe.whitelist()
def get_branch_sla_summary(store, date=None) -> dict:
    """Get SLA summary for a branch on a given date."""
    require_configured_role("dashboard_roles", action=_("view branch SLA details"))
    _require_sla_read("Buyback Order")
    _require_sla_read("CH Store")
    store_row = frappe.db.get_value(
        "CH Store",
        store,
        ["name", "warehouse", "company", "disabled"],
        as_dict=True,
    )
    if not store_row:
        store_row = frappe.db.get_value(
            "CH Store",
            {"warehouse": store, "disabled": 0},
            ["name", "warehouse", "company", "disabled"],
            as_dict=True,
        )
    if not store_row or store_row.disabled:
        frappe.throw(_("The selected store is missing or disabled."), frappe.ValidationError)
    assert_buyback_scope(
        store=store_row.name,
        warehouse=store_row.warehouse,
        company=store_row.company,
    )
    date = getdate(date or nowdate())
    next_date = add_days(date, 1)
    order_limit = get_int_setting("sla_summary_order_limit", 500)

    orders = frappe.get_all(
        "Buyback Order",
        filters={
            "store": ("in", [store_row.name, store_row.warehouse]),
            "creation": ("between", [f"{date} 00:00:00", f"{next_date} 00:00:00"]),
            "docstatus": ["<", 2],
        },
        fields=["name", "status", "creation", "approval_date", "otp_verified_at"],
        limit_page_length=order_limit,
    )

    total = len(orders)
    breaches = 0
    warnings = 0
    on_time = 0

    for order in orders:
        if order.approval_date:
            sla = calculate_sla_status(
                order.creation, order.approval_date,
                _sla_target("confirmation_to_approval")
            )
        else:
            sla = calculate_sla_status(
                order.creation, None,
                _sla_target("confirmation_to_approval")
            )

        if sla["status"] == "Breach":
            breaches += 1
        elif sla["status"] == "Warning":
            warnings += 1
        else:
            on_time += 1

    return {
        "store": store_row.name,
        "date": str(date),
        "total_orders": total,
        "on_time": on_time,
        "warnings": warnings,
        "breaches": breaches,
        "compliance_pct": round(on_time / total * 100, 1) if total else 100,
    }
