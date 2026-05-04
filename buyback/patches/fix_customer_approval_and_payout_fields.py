import frappe


def execute():
    frappe.db.set_value(
        "DocField",
        {"parent": "Buyback Order", "fieldname": "customer_payout_updated_by"},
        {"fieldtype": "Data", "options": None},
        update_modified=False,
    )

    frappe.db.sql(
        """UPDATE `tabBuyback Order`
           SET customer_approval_method = 'App Confirmation'
           WHERE customer_approval_method = 'OTP'"""
    )
    frappe.clear_cache(doctype="Buyback Order")
