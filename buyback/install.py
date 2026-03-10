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
    # Create custom fields on Serial No etc.
    from buyback.custom_fields import setup_custom_fields
    setup_custom_fields()
    create_reporting_indexes()


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


# ── Reporting indexes ──────────────────────────────────────────
# These are composite indexes that speed up the 25 buyback reports
# and dashboard API queries. Safe to run repeatedly (IF NOT EXISTS).

REPORT_INDEXES = [
    # Buyback Assessment
    ("tabBuyback Assessment", "idx_bba_store_creation", ["store", "creation"]),
    ("tabBuyback Assessment", "idx_bba_source_creation", ["source", "creation"]),
    ("tabBuyback Assessment", "idx_bba_imei", ["imei_serial"]),
    ("tabBuyback Assessment", "idx_bba_status_creation", ["status", "creation"]),
    # Buyback Order
    ("tabBuyback Order", "idx_bbo_store_creation", ["store", "creation"]),
    ("tabBuyback Order", "idx_bbo_status_creation", ["status", "creation"]),
    ("tabBuyback Order", "idx_bbo_settlement_status", ["settlement_type", "status"]),
    ("tabBuyback Order", "idx_bbo_custapproved", ["customer_approved", "status"]),
    # Buyback Inspection
    ("tabBuyback Inspection", "idx_bbi_status_creation", ["status", "creation"]),
    ("tabBuyback Inspection", "idx_bbi_inspector", ["inspector", "creation"]),
    ("tabBuyback Inspection", "idx_bbi_mismatch", ["mismatch_percentage"]),
    # SLA Log
    ("tabBuyback SLA Log", "idx_sla_breached", ["breached", "creation"]),
    ("tabBuyback SLA Log", "idx_sla_stage", ["sla_stage", "creation"]),
    # Audit Log
    ("tabBuyback Audit Log", "idx_bal_action", ["action", "creation"]),
    # OTP Log
    ("tabCH OTP Log", "idx_otp_status_creation", ["status", "creation"]),
]


def create_reporting_indexes():
    """Create composite indexes for report performance. Safe to call repeatedly."""
    for table, idx_name, columns in REPORT_INDEXES:
        cols = ", ".join(f"`{c}`" for c in columns)
        try:
            frappe.db.sql_ddl(
                f"CREATE INDEX IF NOT EXISTS `{idx_name}` ON `{table}` ({cols})"
            )
        except Exception:
            # Table may not exist yet during install; ignore
            pass
