"""Seed the 'Buyback Price Override' CH Exception Type, routed to the Buyback Manager.

Buyback exceptions are price-change requests (mirrors POS 'Discount Override'):
a store proposes a different buyback price for a device and it routes to the
Buyback Manager for approval, scoped by the requested amount. Idempotent.
"""
import frappe

NAME = "Buyback Price Override"
LEGACY = "Buyback Exception"  # earlier generic type — superseded by the priced one

DEFAULTS = {
    "enabled": 1,
    "routing_mode": "Approval Matrix",
    "alert_role": "Buyback Manager",
    "notify_team_role": "Buyback Manager",
    "requires_otp": 0,
    "requires_ho_approval": 0,
    "validity_minutes": 1440,
    "escalation_sla_minutes": 120,
    "max_occurrences_per_day": 0,
    "applicable_to_ggr": 1,
}


def execute():
    if not frappe.db.exists("DocType", "CH Exception Type"):
        return
    if not frappe.db.exists("Role", "Buyback Manager"):
        return

    meta = frappe.get_meta("CH Exception Type")
    fields = {k: v for k, v in DEFAULTS.items() if meta.has_field(k)}

    if frappe.db.exists("CH Exception Type", NAME):
        doc = frappe.get_doc("CH Exception Type", NAME)
        for k, v in fields.items():
            doc.set(k, v)
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({"doctype": "CH Exception Type", "exception_type": NAME, **fields})
        doc.insert(ignore_permissions=True)

    # Retire the earlier generic type if it was seeded and is unused.
    if frappe.db.exists("CH Exception Type", LEGACY):
        used = frappe.db.exists("CH Exception Request", {"exception_type": LEGACY})
        if used:
            frappe.db.set_value("CH Exception Type", LEGACY, "enabled", 0)
        else:
            frappe.delete_doc("CH Exception Type", LEGACY, force=True, ignore_permissions=True)

    frappe.db.commit()
