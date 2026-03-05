"""
Shared utilities for the Buyback app.
Pattern: India Compliance keeps utils.py at app root with reusable helpers.
"""

import json

import frappe
from frappe.utils import now_datetime


def log_audit(
    action: str,
    reference_doctype: str,
    reference_name: str,
    old_value: dict | None = None,
    new_value: dict | None = None,
    reason: str | None = None,
):
    """
    Create a Buyback Audit Log entry.

    Centralised helper — replaces the duplicated ``_log_audit()`` that was
    copy-pasted into every controller module.
    """
    frappe.get_doc(
        {
            "doctype": "Buyback Audit Log",
            "action": action,
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "user": frappe.session.user,
            "timestamp": now_datetime(),
            "ip_address": getattr(frappe.local, "request_ip", None),
            "old_value": json.dumps(old_value) if old_value else None,
            "new_value": json.dumps(new_value) if new_value else None,
            "reason": reason,
        }
    ).insert(ignore_permissions=True)


def get_buyback_settings() -> "frappe.Document":
    """Return the cached Buyback Settings singleton."""
    return frappe.get_cached_doc("Buyback Settings")
