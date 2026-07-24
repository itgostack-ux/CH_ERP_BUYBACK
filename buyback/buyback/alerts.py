# Copyright (c) 2026, GoStack and contributors
# Buyback Alerts — SLA breach, high-value, duplicate IMEI, daily summary

"""
Alert channels:
1. ERPNext Notification (in-app bell + email)
2. Frappe realtime publish (live desktop toast)
3. WhatsApp webhook (optional, via Buyback SLA Settings)
"""

import frappe
from frappe import _
from frappe.utils import (
    nowdate, now_datetime, get_datetime, add_days,
    get_url_to_form, flt, cint, fmt_money,
)
import json

from buyback.utils import (
    ROLE_SETTING_DEFAULTS,
    filter_enabled_system_users,
    get_int_setting,
    get_role_setting,
    claim_scheduler_alert,
    new_scheduler_alert_budget,
)
from buyback.outbound_security import post_whatsapp_webhook


# ─── Alert Dispatcher ────────────────────────────────────────────────

def send_alert(subject, message, recipients=None, doctype=None, docname=None,
               alert_type="Warning", send_whatsapp=False, send_email=False):
    """Central alert dispatcher — sends via available channels."""
    recipients = list(dict.fromkeys(recipients or []))

    # 1. ERPNext Notification Log
    try:
        for user in recipients:
            doc = frappe.new_doc("Notification Log")
            doc.subject = subject
            doc.email_content = message
            doc.for_user = user
            doc.type = "Alert"
            if doctype and docname:
                doc.document_type = doctype
                doc.document_name = docname
            doc.insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Buyback notification log delivery failed")

    # 2. Realtime push
    for user in recipients:
        try:
            frappe.publish_realtime(
                "msgprint",
                {"message": message, "title": subject, "indicator": _indicator(alert_type)},
                user=user,
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Buyback realtime alert delivery failed")

    # 3. Email (if explicitly enabled)
    if send_email and recipients:
        try:
            frappe.sendmail(
                recipients=recipients,
                subject=subject,
                message=message,
                reference_doctype=doctype,
                reference_name=docname,
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), title=f"Alert email failed: {subject}")

    # 4. WhatsApp (if enabled)
    if send_whatsapp:
        _send_whatsapp(subject, message)


def _indicator(alert_type):
    return {"Warning": "orange", "Critical": "red", "Info": "blue"}.get(alert_type, "orange")


def _send_whatsapp(subject, message):
    """Send via WhatsApp webhook if configured."""
    try:
        settings = frappe.get_single("Buyback SLA Settings")
        if not settings.enable_whatsapp_alerts or not settings.whatsapp_webhook_url:
            return
        payload = {
            "text": f"*{subject}*\n{frappe.utils.strip_html(message)}",
        }
        post_whatsapp_webhook(
            settings.whatsapp_webhook_url,
            settings.get("whatsapp_allowed_hosts"),
            payload,
            timeout=10,
        )
    except Exception:
        frappe.log_error(title="WhatsApp alert failed")


# ─── Specific Alert Functions ────────────────────────────────────────

def alert_sla_breach(doctype, docname, sla_type, minutes_taken, target_minutes):
    """Fire alert for SLA breach."""
    url = get_url_to_form(doctype, docname)
    subject = f"⚠️ SLA Breach: {sla_type.replace('_', ' ').title()}"
    message = (
        f"<b>{doctype}</b> <a href='{url}'>{docname}</a> has breached SLA. "
        f"<br>SLA: {sla_type.replace('_', ' ').title()}"
        f"<br>Target: {target_minutes} min | Actual: {minutes_taken:.0f} min"
    )
    # Resolve store from the document for scope-based recipient filtering
    store = frappe.db.get_value(doctype, docname, "store") if doctype and docname else None
    recipients = _get_alert_recipients(
        doctype,
        docname,
        _configured_alert_roles("sla_alert_roles"),
        store=store,
    )
    send_alert(subject, message, recipients, doctype, docname, "Critical", send_whatsapp=True)


def alert_high_value_order(docname, final_price, threshold):
    """Alert for high-value buyback order needing extra scrutiny."""
    url = get_url_to_form("Buyback Order", docname)
    subject = f"💰 High-Value Order: ₹{flt(final_price):,.0f}"
    message = (
        f"Buyback Order <a href='{url}'>{docname}</a> has value "
        f"₹{flt(final_price):,.0f} (threshold: ₹{flt(threshold):,.0f}). "
        f"<br>Please review and approve."
    )
    store = frappe.db.get_value("Buyback Order", docname, "store")
    recipients = _get_alert_recipients(
        "Buyback Order", docname, _configured_alert_roles("approval_alert_roles"), store=store
    )
    send_alert(subject, message, recipients, "Buyback Order", docname, "Warning")


def alert_manager_approval_required(docname, final_price=None, threshold=None):
    """Email scoped managers when an order enters Awaiting Approval."""
    cache_key = f"manager_approval_required_{docname}"
    if frappe.cache.get_value(cache_key):
        return

    row = frappe.db.get_value(
        "Buyback Order",
        docname,
        ["store", "customer_name", "item_name", "final_price"],
        as_dict=True,
    )
    if not row:
        return

    final_price = flt(final_price if final_price is not None else row.final_price)
    threshold = flt(threshold)
    url = get_url_to_form("Buyback Order", docname)
    subject = f"Manager Approval Required: {docname}"
    message = (
        f"Buyback Order <a href='{url}'>{docname}</a> requires manager approval."
        f"<br>Amount: {fmt_money(final_price, currency='INR')}"
        f"<br>Threshold: {fmt_money(threshold, currency='INR')}"
        f"<br>Customer: {frappe.utils.escape_html(row.customer_name or '-')}"
        f"<br>Device: {frappe.utils.escape_html(row.item_name or '-')}"
    )

    recipients = _get_alert_recipients(
        "Buyback Order",
        docname,
        _configured_alert_roles("approval_alert_roles"),
        store=row.store,
        include_owner=False,
    )
    if not recipients:
        return

    send_alert(
        subject,
        message,
        recipients,
        "Buyback Order",
        docname,
        "Warning",
        send_email=True,
    )
    frappe.cache.set_value(cache_key, 1, expires_in_sec=7 * 86400)


def alert_duplicate_imei(imei, order_names):
    """Alert for IMEI appearing in multiple orders."""
    subject = f"🔴 Duplicate IMEI Detected: {imei}"
    message = (
        f"IMEI/Serial <b>{imei}</b> found in multiple orders: "
        f"{', '.join(order_names)}. <br>Please investigate for potential fraud."
    )
    recipients = _get_alert_recipients(
        None, None, _configured_alert_roles("fraud_alert_roles")
    )
    send_alert(subject, message, recipients, alert_type="Critical", send_whatsapp=True)


def alert_daily_cash_limit(store, total_cash, limit):
    """Alert when branch approaches/exceeds daily cash limit."""
    subject = f"💵 Cash Limit Alert: {store}"
    message = (
        f"Branch <b>{store}</b> has processed "
        f"₹{flt(total_cash):,.0f} in cash today "
        f"(limit: ₹{flt(limit):,.0f})."
    )
    recipients = _get_alert_recipients(
        None, None, _configured_alert_roles("cash_alert_roles"), store=store
    )
    send_alert(subject, message, recipients, alert_type="Warning")


def alert_low_conversion(store, conversion_pct, threshold):
    """Alert when store conversion drops below threshold."""
    subject = f"📉 Low Conversion: {store} ({conversion_pct:.0f}%)"
    message = (
        f"Branch <b>{store}</b> conversion rate is at "
        f"<b>{conversion_pct:.1f}%</b> (threshold: {threshold}%). "
        f"<br>Please review operations."
    )
    recipients = _get_alert_recipients(
        None, None, _configured_alert_roles("performance_alert_roles"), store=store
    )
    send_alert(subject, message, recipients, alert_type="Warning")


def alert_inspection_backlog(store, pending_count, threshold):
    """Alert when inspections pile up."""
    subject = f"📋 Inspection Backlog: {store} ({pending_count} pending)"
    message = (
        f"Branch <b>{store}</b> has <b>{pending_count}</b> pending inspections "
        f"(alert threshold: {threshold}). Please clear the backlog."
    )
    recipients = _get_alert_recipients(
        None, None, _configured_alert_roles("performance_alert_roles"), store=store
    )
    send_alert(subject, message, recipients, alert_type="Warning")


# ─── Scheduled alert checks ─────────────────────────────────────────

def check_daily_alerts():
    """Scheduled daily — cash limits, conversion, inspection backlog."""
    alert_budget = new_scheduler_alert_budget()
    for check in (_check_cash_limits, _check_conversion_rates, _check_inspection_backlogs):
        try:
            check(alert_budget)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Buyback daily alert check failed: {check.__name__}")


def _check_cash_limits(alert_budget=None):
    """Check if any branch is nearing daily cash limit."""
    try:
        settings = frappe.get_single("Buyback SLA Settings")
        daily_limit = flt(settings.daily_cash_limit_per_branch) or 200000
    except frappe.DoesNotExistError:
        daily_limit = 200000
    alert_pct = min(get_int_setting("cash_limit_alert_pct", 80), 100)

    today = nowdate()
    branches = frappe.db.sql("""
        SELECT o.store, SUM(p.amount) as cash_total
        FROM `tabBuyback Order Payment` p
        JOIN `tabBuyback Order` o ON o.name = p.parent
        WHERE p.payment_method LIKE '%%Cash%%'
            AND DATE(p.payment_date) = %(today)s
            AND IFNULL(o.store, '') != ''
        GROUP BY o.store
        HAVING cash_total > %(threshold)s
        ORDER BY cash_total DESC, o.store ASC
        LIMIT %(limit)s
    """, {
        "today": today,
        "threshold": daily_limit * alert_pct / 100,
        "limit": min(get_int_setting("scheduler_batch_limit", 500), 5000),
    }, as_dict=1)

    for b in branches:
        cache_key = f"cash_alert_{b.store}_{today}"
        if not frappe.cache.get_value(cache_key) and claim_scheduler_alert(alert_budget):
            alert_daily_cash_limit(b.store, b.cash_total, daily_limit)
            frappe.cache.set_value(cache_key, 1, expires_in_sec=86400)


def _check_conversion_rates(alert_budget=None):
    """Check branch conversion rates."""
    try:
        settings = frappe.get_single("Buyback SLA Settings")
        threshold = flt(settings.conversion_alert_threshold_pct) or 40
    except frappe.DoesNotExistError:
        threshold = 40

    today = nowdate()
    lookback_days = min(get_int_setting("conversion_alert_lookback_days", 7), 366)
    minimum_assessments = min(
        get_int_setting("conversion_alert_min_assessments", 5), 100000
    )
    week_ago = add_days(today, -lookback_days)
    next_day = add_days(today, 1)

    stores = frappe.db.sql("""
        SELECT assessment.store, assessment.assessments, COALESCE(orders.orders, 0) AS orders
        FROM (
            SELECT store, COUNT(*) AS assessments
            FROM `tabBuyback Assessment`
            WHERE creation >= %(week_ago)s
              AND creation < %(next_day)s
              AND IFNULL(store, '') != ''
            GROUP BY store
            HAVING COUNT(*) > %(minimum_assessments)s
        ) assessment
        LEFT JOIN (
            SELECT store, COUNT(*) AS orders
            FROM `tabBuyback Order`
            WHERE docstatus < 2
              AND creation >= %(week_ago)s
              AND creation < %(next_day)s
              AND IFNULL(store, '') != ''
            GROUP BY store
        ) orders ON orders.store = assessment.store
        ORDER BY assessment.store ASC
        LIMIT %(limit)s
    """, {
        "week_ago": week_ago,
        "next_day": next_day,
        "minimum_assessments": minimum_assessments,
        "limit": min(get_int_setting("scheduler_batch_limit", 500), 5000),
    }, as_dict=1)

    for s in stores:
        if s.assessments:
            conv = round(s.orders / s.assessments * 100, 1)
            if conv < threshold:
                cache_key = f"conv_alert_{s.store}_{today}"
                if not frappe.cache.get_value(cache_key) and claim_scheduler_alert(alert_budget):
                    alert_low_conversion(s.store, conv, threshold)
                    frappe.cache.set_value(cache_key, 1, expires_in_sec=86400)


def _check_inspection_backlogs(alert_budget=None):
    """Check for inspection backlogs."""
    try:
        settings = frappe.get_single("Buyback SLA Settings")
        backlog_threshold = cint(settings.inspection_backlog_alert) or 5
    except frappe.DoesNotExistError:
        backlog_threshold = 5

    backlogs = frappe.db.sql("""
        SELECT store, COUNT(*) as pending
        FROM `tabBuyback Inspection`
        WHERE status IN ('Pending', 'In Progress')
          AND IFNULL(store, '') != ''
        GROUP BY store
        HAVING pending >= %s
        ORDER BY pending DESC, store ASC
        LIMIT %s
    """, (
        backlog_threshold,
        min(get_int_setting("scheduler_batch_limit", 500), 5000),
    ), as_dict=1)

    today = nowdate()
    for b in backlogs:
        cache_key = f"backlog_alert_{b.store}_{today}"
        if not frappe.cache.get_value(cache_key) and claim_scheduler_alert(alert_budget):
            alert_inspection_backlog(b.store, b.pending, backlog_threshold)
            frappe.cache.set_value(cache_key, 1, expires_in_sec=86400)


# ─── Helpers ─────────────────────────────────────────────────────────

def _configured_alert_roles(fieldname):
    """Return the site-configured role set for one alert category."""
    return list(get_role_setting(fieldname, ROLE_SETTING_DEFAULTS[fieldname]))


def _alert_recipient_limit():
    return min(get_int_setting("alert_recipient_limit", 15), 500)


def _get_alert_recipients(doctype=None, docname=None, roles=None, store=None, include_owner=True):
    """Get alert recipients: role-holders scoped to `store` + optional doc owner.

    When `store` is provided, only users whose CH User Scope includes that store
    are included — equivalent to SAP S/4HANA plant-based notification scoping
    and Oracle NetSuite role+subsidiary targeting. Global-scope recipients are
    included regardless of store scope.

    Falls back to unscoped role lookup if notification_router is unavailable.
    """
    users = set()

    # Document owner always included (Odoo followers-model equivalent)
    if include_owner and doctype and docname:
        try:
            owner = frappe.db.get_value(doctype, docname, "owner")
            if owner:
                users.add(owner)
        except frappe.DoesNotExistError:
            pass

    # Role × Scope intersection via notification_router
    try:
        from ch_erp15.ch_erp15.notification_router import get_scoped_users
        role_users = get_scoped_users(roles or [], store=store)
        users.update(role_users)
    except Exception:
        # SECURITY: never broadcast store-scoped alerts to all role users.
        if store:
            return filter_enabled_system_users(users, limit=_alert_recipient_limit())

        # Non-store alerts may fallback to plain role lookup.
        role_rows = frappe.get_all(
            "Has Role",
            filters={"role": ("in", list(roles or [])), "parenttype": "User"},
            pluck="parent",
            limit=_alert_recipient_limit(),
        ) if roles else []
        users.update(user for user in role_rows if user)

    return filter_enabled_system_users(users, limit=_alert_recipient_limit())
