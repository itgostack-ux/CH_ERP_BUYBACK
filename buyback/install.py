"""
Post-install hooks for the Buyback app.
Creates custom roles and seed data required by the buyback workflow.
"""

import frappe
from frappe import _


BUYBACK_ROLES = [
    {
        "role_name": "Buyback Agent",
        "desk_access": 1,
        "is_custom": 1,
        "search_bar": 1,
        "notifications": 1,
    },
    {
        "role_name": "Buyback Manager",
        "desk_access": 1,
        "is_custom": 1,
        "search_bar": 1,
        "notifications": 1,
    },
    {
        "role_name": "Buyback Auditor",
        "desk_access": 1,
        "is_custom": 1,
        "search_bar": 1,
        "notifications": 1,
    },
    {
        "role_name": "Buyback Admin",
        "desk_access": 1,
        "is_custom": 1,
        "search_bar": 1,
        "notifications": 1,
    },
    {
        "role_name": "Buyback Store Manager",
        "desk_access": 1,
        "is_custom": 1,
        "search_bar": 1,
        "notifications": 1,
    },
]


def after_install():
    """Run after the Buyback app is installed."""
    _create_roles()
    _create_default_settings()


def _create_roles():
    """Create buyback-specific roles if they don't already exist."""
    for role_def in BUYBACK_ROLES:
        if not frappe.db.exists("Role", role_def["role_name"]):
            doc = frappe.get_doc({"doctype": "Role", **role_def})
            doc.insert(ignore_permissions=True)
            frappe.logger().info(f"Created role: {role_def['role_name']}")


def _create_default_settings():
    """Seed Buyback Settings with sensible defaults (if empty)."""
    settings = frappe.get_single("Buyback Settings")
    if not settings.quote_validity_days:
        settings.quote_validity_days = 7
    if not settings.otp_expiry_minutes:
        settings.otp_expiry_minutes = 10
    if not settings.max_otp_attempts:
        settings.max_otp_attempts = 3
    if not settings.require_manager_approval_above:
        settings.require_manager_approval_above = 50000
    settings.require_otp_for_payment = 1
    settings.enable_audit_log = 1
    settings.save(ignore_permissions=True)
