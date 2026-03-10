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
import requests


# ─── Alert Dispatcher ────────────────────────────────────────────────

def send_alert(subject, message, recipients=None, doctype=None, docname=None,
               alert_type="Warning", send_whatsapp=False):
    """Central alert dispatcher — sends via available channels."""

    # 1. ERPNext Notification Log
    try:
        for user in (recipients or []):
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
        pass

    # 2. Realtime push
    for user in (recipients or []):
        try:
            frappe.publish_realtime(
                "msgprint",
                {"message": message, "title": subject, "indicator": _indicator(alert_type)},
                user=user,
            )
        except Exception:
            pass

    # 3. WhatsApp (if enabled)
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
        requests.post(
            settings.whatsapp_webhook_url,
            json=payload,
            timeout=10,
        )
    except Exception:
        frappe.log_error("WhatsApp alert failed")


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

    # Get store manager and admin users
    recipients = _get_alert_recipients(doctype, docname, ["Buyback Manager", "Buyback Admin"])
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
    recipients = _get_alert_recipients("Buyback Order", docname, ["Buyback Manager", "Buyback Admin"])
    send_alert(subject, message, recipients, "Buyback Order", docname, "Warning")


def alert_duplicate_imei(imei, order_names):
    """Alert for IMEI appearing in multiple orders."""
    subject = f"🔴 Duplicate IMEI Detected: {imei}"
    message = (
        f"IMEI/Serial <b>{imei}</b> found in multiple orders: "
        f"{', '.join(order_names)}. <br>Please investigate for potential fraud."
    )
    recipients = _get_alert_recipients(None, None, ["Buyback Admin", "Buyback Auditor"])
    send_alert(subject, message, recipients, alert_type="Critical", send_whatsapp=True)


def alert_daily_cash_limit(store, total_cash, limit):
    """Alert when branch approaches/exceeds daily cash limit."""
    subject = f"💵 Cash Limit Alert: {store}"
    message = (
        f"Branch <b>{store}</b> has processed "
        f"₹{flt(total_cash):,.0f} in cash today "
        f"(limit: ₹{flt(limit):,.0f})."
    )
    recipients = _get_alert_recipients(None, None, ["Buyback Manager", "Buyback Admin"])
    send_alert(subject, message, recipients, alert_type="Warning")


def alert_low_conversion(store, conversion_pct, threshold):
    """Alert when store conversion drops below threshold."""
    subject = f"📉 Low Conversion: {store} ({conversion_pct:.0f}%)"
    message = (
        f"Branch <b>{store}</b> conversion rate is at "
        f"<b>{conversion_pct:.1f}%</b> (threshold: {threshold}%). "
        f"<br>Please review operations."
    )
    recipients = _get_alert_recipients(None, None, ["Buyback Manager", "Buyback Store Manager"])
    send_alert(subject, message, recipients, alert_type="Warning")


def alert_inspection_backlog(store, pending_count, threshold):
    """Alert when inspections pile up."""
    subject = f"📋 Inspection Backlog: {store} ({pending_count} pending)"
    message = (
        f"Branch <b>{store}</b> has <b>{pending_count}</b> pending inspections "
        f"(alert threshold: {threshold}). Please clear the backlog."
    )
    recipients = _get_alert_recipients(None, None, ["Buyback Manager", "Buyback Store Manager"])
    send_alert(subject, message, recipients, alert_type="Warning")


# ─── Scheduled alert checks ─────────────────────────────────────────

def check_daily_alerts():
    """Scheduled daily — cash limits, conversion, inspection backlog."""
    _check_cash_limits()
    _check_conversion_rates()
    _check_inspection_backlogs()


def _check_cash_limits():
    """Check if any branch is nearing daily cash limit."""
    try:
        settings = frappe.get_single("Buyback SLA Settings")
        daily_limit = flt(settings.daily_cash_limit_per_branch) or 200000
    except Exception:
        daily_limit = 200000

    today = nowdate()
    branches = frappe.db.sql(f"""
        SELECT o.store, SUM(p.amount) as cash_total
        FROM `tabBuyback Order Payment` p
        JOIN `tabBuyback Order` o ON o.name = p.parent
        WHERE p.payment_method LIKE '%%Cash%%'
            AND DATE(p.payment_date) = '{today}'
        GROUP BY o.store
        HAVING cash_total > {daily_limit * 0.8}
    """, as_dict=1)

    for b in branches:
        cache_key = f"cash_alert_{b.store}_{today}"
        if not frappe.cache.get_value(cache_key):
            alert_daily_cash_limit(b.store, b.cash_total, daily_limit)
            frappe.cache.set_value(cache_key, 1, expires_in_sec=86400)


def _check_conversion_rates():
    """Check branch conversion rates."""
    try:
        settings = frappe.get_single("Buyback SLA Settings")
        threshold = flt(settings.conversion_alert_threshold_pct) or 40
    except Exception:
        threshold = 40

    today = nowdate()
    week_ago = add_days(today, -7)

    stores = frappe.db.sql(f"""
        SELECT store,
            (SELECT COUNT(*) FROM `tabBuyback Assessment`
             WHERE store = o.store AND creation BETWEEN '{week_ago}' AND '{today} 23:59:59') as assessments,
            COUNT(*) as orders
        FROM `tabBuyback Order` o
        WHERE docstatus < 2
            AND creation BETWEEN '{week_ago}' AND '{today} 23:59:59'
        GROUP BY store
    """, as_dict=1)

    for s in stores:
        if s.assessments and s.assessments > 5:
            conv = round(s.orders / s.assessments * 100, 1)
            if conv < threshold:
                cache_key = f"conv_alert_{s.store}_{today}"
                if not frappe.cache.get_value(cache_key):
                    alert_low_conversion(s.store, conv, threshold)
                    frappe.cache.set_value(cache_key, 1, expires_in_sec=86400)


def _check_inspection_backlogs():
    """Check for inspection backlogs."""
    try:
        settings = frappe.get_single("Buyback SLA Settings")
        backlog_threshold = cint(settings.inspection_backlog_alert) or 5
    except Exception:
        backlog_threshold = 5

    backlogs = frappe.db.sql("""
        SELECT store, COUNT(*) as pending
        FROM `tabBuyback Inspection`
        WHERE status IN ('Pending', 'In Progress')
        GROUP BY store
        HAVING pending >= %s
    """, (backlog_threshold,), as_dict=1)

    today = nowdate()
    for b in backlogs:
        cache_key = f"backlog_alert_{b.store}_{today}"
        if not frappe.cache.get_value(cache_key):
            alert_inspection_backlog(b.store, b.pending, backlog_threshold)
            frappe.cache.set_value(cache_key, 1, expires_in_sec=86400)


# ─── Helpers ─────────────────────────────────────────────────────────

def _get_alert_recipients(doctype=None, docname=None, roles=None):
    """Get users with given roles. If doctype/docname given, prefer the doc owner + store users."""
    users = set()

    # Doc owner
    if doctype and docname:
        try:
            owner = frappe.db.get_value(doctype, docname, "owner")
            if owner:
                users.add(owner)
        except Exception:
            pass

    # Role-based
    for role in (roles or []):
        try:
            role_users = frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"},
                                         fields=["parent"], limit=20)
            for u in role_users:
                if u.parent and u.parent != "Administrator" and "@" in u.parent:
                    users.add(u.parent)
        except Exception:
            pass

    return list(users)[:10]  # Cap at 10 recipients
