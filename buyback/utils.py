"""
Shared utilities for the Buyback app.
Pattern: India Compliance keeps utils.py at app root with reusable helpers.
"""

import json

import frappe
from frappe import _
from frappe.utils import now_datetime


# ---------------------------------------------------------------------------
# Indian phone number validation — canonical home is ch_item_master.utils
# Re-exported here for backward compatibility.
# ---------------------------------------------------------------------------
from ch_item_master.ch_item_master.utils import (  # noqa: F401
    normalize_indian_phone,
    validate_indian_phone,
)


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
    meta = frappe.get_meta("Buyback Audit Log")

    # Keep action compatible with strict Select options in customized sites.
    action_value = action
    action_df = meta.get_field("action")
    if action_df and action_df.fieldtype == "Select":
        allowed_actions = {row.strip() for row in (action_df.options or "").split("\n") if row.strip()}
        if allowed_actions and action_value not in allowed_actions:
            fallback = "Settlement Done" if "Settlement Done" in allowed_actions else next(iter(allowed_actions))
            action_value = fallback
            reason = f"{reason + ' | ' if reason else ''}Original Action: {action}"

    payload = {
        "doctype": "Buyback Audit Log",
        "action": action_value,
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "user": frappe.session.user,
        "timestamp": now_datetime(),
        "ip_address": getattr(frappe.local, "request_ip", None),
        "old_value": json.dumps(old_value) if old_value else None,
        "new_value": json.dumps(new_value) if new_value else None,
        "reason": reason,
    }

    # Some sites keep a custom Select field `condition_grade` on Buyback Audit Log.
    # Normalize Grade Master link (e.g. GRD-00003) to label (e.g. A - Like New).
    grade_df = meta.get_field("condition_grade")
    if grade_df and reference_doctype == "Buyback Order" and frappe.db.exists("Buyback Order", reference_name):
        raw_grade = frappe.db.get_value("Buyback Order", reference_name, "condition_grade")
        if raw_grade:
            grade_name = frappe.db.get_value("Grade Master", raw_grade, "grade_name") or raw_grade
            payload["condition_grade"] = grade_name

    frappe.get_doc(payload).insert(ignore_permissions=True)


def get_buyback_settings() -> "frappe.Document":
    """Return the cached Buyback Settings singleton."""
    return frappe.get_cached_doc("Buyback Settings")
