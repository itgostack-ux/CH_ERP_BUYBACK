"""
Shared utilities for the Buyback app.
Pattern: India Compliance keeps utils.py at app root with reusable helpers.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import now_datetime


# ---------------------------------------------------------------------------
# Indian phone number validation
# ---------------------------------------------------------------------------

# Matches a bare 10-digit Indian mobile number (starts with 6-9)
_INDIAN_PHONE_RE = re.compile(r"^[6-9]\d{9}$")


def normalize_indian_phone(raw: str) -> str:
    """Strip whitespace, dashes, dots, parentheses, and country-code prefix.

    Accepted input formats (all normalise to a bare 10-digit string):
      - 9989898901
      - 09989898901           (leading 0)
      - +91 9989898901
      - +91-9989898901
      - +919989898901
      - 0091 9989898901
      - +91 98899 89901       (spaces mid-number)
      - +91 9889 989 901      (any internal spacing)

    Returns the normalised 10-digit string, or the stripped input (for the
    caller to reject as invalid).
    """
    s = re.sub(r"[\s\-().]", "", raw or "")
    if s.startswith("+91"):
        s = s[3:]
    elif s.startswith("0091"):
        s = s[4:]
    elif s.startswith("91") and len(s) == 12:
        # e.g. 919989898901 — bare 91 prefix without + or 00
        s = s[2:]
    if s.startswith("0") and len(s) == 11:
        # Leading trunk digit: 09989898901
        s = s[1:]
    return s


def validate_indian_phone(raw: str, field_label: str = "Mobile number") -> str:
    """Validate and normalise an Indian mobile number.

    Accepts all common Indian formatting variants (see normalize_indian_phone).
    Raises frappe.ValidationError on invalid input.

    Returns the normalised bare 10-digit string.
    """
    if not raw or not str(raw).strip():
        frappe.throw(_("{0} is required.").format(field_label))

    digits = normalize_indian_phone(str(raw))
    if not _INDIAN_PHONE_RE.match(digits):
        frappe.throw(
            _(
                "{0} '{1}' is not a valid Indian mobile number. "
                "Please enter a 10-digit number starting with 6–9 "
                "(e.g. 9876543210, +91 9876543210, 09876543210)."
            ).format(field_label, raw)
        )
    return digits


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
