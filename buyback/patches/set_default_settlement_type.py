"""
Patch: Set default settlement_type on existing Buyback Orders.

All pre-existing orders were pure buyback (cash payout),
so we set settlement_type = 'Buyback'.
"""

import frappe


def execute():
    if not frappe.db.has_column("Buyback Order", "settlement_type"):
        return

    frappe.db.sql("""
        UPDATE `tabBuyback Order`
        SET settlement_type = 'Buyback'
        WHERE settlement_type IS NULL OR settlement_type = ''
    """)
    frappe.db.commit()
