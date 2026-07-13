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
    seed_grade_master()
    # Create custom fields on Serial No etc.
    from buyback.custom_fields import setup_custom_fields
    setup_custom_fields()
    create_reporting_indexes()


def before_install():
    """Pre-flight hardening before fixtures import.

    Some benches can carry a stale/null cache entry for System Settings;
    then `frappe.get_system_settings("time_zone")` returns None-object and
    fixture insert crashes while setting modified timestamp (`now()`).

    Ensure the singleton exists, has a timezone, and clear any stale cache.
    """
    _ensure_system_settings_ready()


def _ensure_system_settings_ready():
    try:
        from frappe.core.doctype.system_settings.system_settings import (
            clear_system_settings_cache,
            sync_system_settings,
        )
    except Exception:
        clear_system_settings_cache = None  # type: ignore[assignment]
        sync_system_settings = None  # type: ignore[assignment]

    if sync_system_settings:
        try:
            sync_system_settings()
        except Exception:
            pass

    # Materialize singleton doc in this request context.
    try:
        frappe.get_single("System Settings")
    except Exception:
        pass

    # Keep timezone non-empty for now()/ZoneInfo during fixture import.
    try:
        if not frappe.db.get_single_value("System Settings", "time_zone"):
            frappe.db.set_single_value(
                "System Settings",
                "time_zone",
                (frappe.conf.get("time_zone") or "Asia/Kolkata"),
                update_modified=False,
            )
    except Exception:
        pass

    if clear_system_settings_cache:
        try:
            clear_system_settings_cache()
        except Exception:
            pass

    frappe.db.commit()


GRADE_MASTER_SEED = [
    {"grade_name": "A", "description": "Like new / Excellent condition", "display_order": 1},
    {"grade_name": "B", "description": "Good condition, minor cosmetic marks", "display_order": 2},
    {"grade_name": "C", "description": "Fair condition, visible wear", "display_order": 3},
    {"grade_name": "D", "description": "Poor condition, significant damage", "display_order": 4},
]


def seed_grade_master():
    """Ensure standard A/B/C/D grades exist. Safe to run repeatedly."""
    for g in GRADE_MASTER_SEED:
        if not frappe.db.exists("Grade Master", {"grade_name": g["grade_name"]}):
            doc = frappe.get_doc({"doctype": "Grade Master", **g})
            doc.insert(ignore_permissions=True)
            frappe.logger().info(f"Seeded Grade Master: {g['grade_name']}")
    frappe.db.commit()


def _create_roles():
    """Create buyback-specific roles if they don't already exist."""
    for role_def in BUYBACK_ROLES:
        if not frappe.db.exists("Role", role_def["role_name"]):
            doc = frappe.get_doc({"doctype": "Role", **role_def})
            doc.insert(ignore_permissions=True)
            frappe.logger().info(f"Created role: {role_def['role_name']}")


def sync_default_settings():
    """Migration-safe wrapper for default Buyback Settings."""
    _create_default_settings()


def _create_default_settings():
    """Seed Buyback Settings with sensible defaults (if empty)."""
    settings = frappe.get_single("Buyback Settings")
    if settings.buyback_warehouse and not frappe.db.exists("Warehouse", settings.buyback_warehouse):
        frappe.logger("buyback").warning(
            "Clearing stale Buyback Settings.buyback_warehouse: %s",
            settings.buyback_warehouse,
        )
        settings.buyback_warehouse = None
    if not settings.quote_validity_days:
        settings.quote_validity_days = 7
    if not settings.otp_expiry_minutes:
        settings.otp_expiry_minutes = 10
    if not settings.max_otp_attempts:
        settings.max_otp_attempts = 3
    if not settings.require_manager_approval_above:
        settings.require_manager_approval_above = 50000
    company = settings.default_company or frappe.defaults.get_global_default("company")
    if company:
        if settings.meta.has_field("buyback_liability_account") and not settings.buyback_liability_account:
            liability_account = frappe.db.get_value(
                "Account",
                {"company": company, "account_name": "Device Buyback Liability", "is_group": 0},
                "name",
            )
            if liability_account:
                settings.buyback_liability_account = liability_account

        expense_root_type = None
        if settings.buyback_expense_account:
            expense_root_type = frappe.db.get_value("Account", settings.buyback_expense_account, "root_type")
        if not settings.buyback_expense_account or expense_root_type != "Expense":
            default_expense_account = frappe.db.get_value("Company", company, "default_expense_account")
            if default_expense_account:
                settings.buyback_expense_account = default_expense_account
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
