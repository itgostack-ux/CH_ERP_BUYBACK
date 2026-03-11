"""
Patch: Rename map_type values from Item/Item Group to Model/Subcategory.
"""

import frappe


def execute():
    if not frappe.db.table_exists("tabBuyback Item Question Map"):
        return
    if not frappe.db.has_column("Buyback Item Question Map", "map_type"):
        return

    frappe.db.sql("""
        UPDATE `tabBuyback Item Question Map`
        SET map_type = 'Model'
        WHERE map_type = 'Item'
    """)
    frappe.db.sql("""
        UPDATE `tabBuyback Item Question Map`
        SET map_type = 'Subcategory'
        WHERE map_type = 'Item Group'
    """)
    frappe.db.commit()
