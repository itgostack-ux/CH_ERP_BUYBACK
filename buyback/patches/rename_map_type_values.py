"""
Patch: Normalize map_type values to UI-facing labels.
"""

import frappe


def execute():
    if not frappe.db.table_exists("tabBuyback Item Question Map"):
        return
    if not frappe.db.has_column("Buyback Item Question Map", "map_type"):
        return

    frappe.db.sql("""
        UPDATE `tabBuyback Item Question Map`
        SET map_type = 'Model Override'
        WHERE map_type IN ('Item', 'Model')
    """)
    frappe.db.sql("""
        UPDATE `tabBuyback Item Question Map`
        SET map_type = 'Subcategory Default'
        WHERE map_type IN ('Item Group', 'Subcategory')
    """)
    frappe.db.commit()
