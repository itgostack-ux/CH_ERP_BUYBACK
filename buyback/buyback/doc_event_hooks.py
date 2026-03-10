# Copyright (c) 2026, GoStack and contributors
# Doc Event Hooks — triggered on Buyback Order save/update

import frappe
from frappe.utils import flt


def on_buyback_order_update(doc, method):
    """Hook called on every Buyback Order update."""
    if frappe.flags.in_demo_data:
        return

    _check_high_value_alert(doc)
    _check_duplicate_imei(doc)


def _check_high_value_alert(doc):
    """Alert if order exceeds large payout threshold."""
    try:
        threshold = frappe.db.get_single_value("Buyback SLA Settings", "large_payout_threshold") or 25000
    except Exception:
        threshold = 25000

    if flt(doc.final_price) > flt(threshold):
        cache_key = f"high_value_alert_{doc.name}"
        if not frappe.cache.get_value(cache_key):
            from buyback.buyback.alerts import alert_high_value_order
            alert_high_value_order(doc.name, doc.final_price, threshold)
            frappe.cache.set_value(cache_key, 1, expires_in_sec=86400)


def _check_duplicate_imei(doc):
    """Check and alert if IMEI has been seen in other orders."""
    if not doc.imei_serial:
        return

    other_orders = frappe.get_all(
        "Buyback Order",
        filters={
            "imei_serial": doc.imei_serial,
            "name": ("!=", doc.name),
            "docstatus": ("<", 2),
        },
        pluck="name",
        limit=5,
    )

    if other_orders:
        cache_key = f"dup_imei_alert_{doc.imei_serial}"
        if not frappe.cache.get_value(cache_key):
            from buyback.buyback.alerts import alert_duplicate_imei
            alert_duplicate_imei(doc.imei_serial, [doc.name] + other_orders)
            frappe.cache.set_value(cache_key, 1, expires_in_sec=86400)
