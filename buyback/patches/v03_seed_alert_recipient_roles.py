"""Seed the five Buyback alert-recipient role lists, then let the DB rule.

Until now `approval/cash/fraud/performance/sla_alert_roles` were hidden Small Text
fields that nobody had ever filled in, so `buyback.utils.ROLE_SETTING_DEFAULTS`
silently decided who got alerted and an admin could not change it. The fields are
now Table MultiSelect -> CH Role Link; this copies the value that was previously
in force (the legacy text if someone had set it, otherwise the shipped default)
so behaviour is unchanged, after which the code no longer carries any default.

Idempotent: a field that already has rows is left alone. Roles that do not exist
on this site are skipped -- seeding them would only create dead rows.
"""
import frappe

from ch_erp15.role_settings import set_setting_roles

DOCTYPE = "Buyback Settings"

# The values that were in force before this patch, lifted verbatim from the old
# buyback.utils.ROLE_SETTING_DEFAULTS so no site changes behaviour on migrate.
PREVIOUS_DEFAULTS = {
    "sla_alert_roles": ("Buyback Admin", "Buyback Manager", "Buyback Store Manager"),
    "approval_alert_roles": ("Buyback Admin", "Buyback Manager"),
    "fraud_alert_roles": ("Buyback Admin", "Buyback Auditor"),
    "cash_alert_roles": ("Buyback Admin", "Buyback Manager"),
    "performance_alert_roles": ("Buyback Manager", "Buyback Store Manager"),
}


def execute():
    if not frappe.db.exists("DocType", DOCTYPE):
        return
    if not frappe.db.table_exists("CH Role Link"):
        # ch_erp15 not migrated yet on this site; the next migrate will pick it up.
        return

    meta = frappe.get_meta(DOCTYPE)
    for fieldname, defaults in PREVIOUS_DEFAULTS.items():
        df = meta.get_field(fieldname)
        if df is None or df.fieldtype != "Table MultiSelect":
            continue

        existing = frappe.db.count(
            df.options,
            {"parent": DOCTYPE, "parenttype": DOCTYPE, "parentfield": fieldname},
        )
        if existing:
            continue

        roles = _legacy_text_roles(fieldname) or list(defaults)
        roles = [r for r in roles if frappe.db.exists("Role", r)]
        if not roles:
            frappe.logger("buyback").info(
                f"{fieldname}: no existing roles to seed; leaving empty (nobody alerted)"
            )
            continue
        set_setting_roles(DOCTYPE, fieldname, roles)

    frappe.db.commit()


def _legacy_text_roles(fieldname):
    """Roles an admin had typed into the old Small Text field, if any."""
    try:
        value = frappe.db.get_value(DOCTYPE, DOCTYPE, fieldname)
    except Exception:
        return []
    if not value:
        return []
    return [part.strip() for part in str(value).replace(",", "\n").split("\n") if part.strip()]
