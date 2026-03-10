# Copyright (c) 2026, GoStack and contributors
# Pending Confirmations — Orders in Awaiting Customer Approval status.
# Redirects to Customer Approval Pending report logic but as a quick list.

import frappe
from frappe import _


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"fieldname": "name", "label": _("Order"), "fieldtype": "Link", "options": "Buyback Order", "width": 160},
        {"fieldname": "store", "label": _("Branch"), "fieldtype": "Link", "options": "Warehouse", "width": 160},
        {"fieldname": "customer_name", "label": _("Customer"), "fieldtype": "Data", "width": 150},
        {"fieldname": "item", "label": _("Model"), "fieldtype": "Link", "options": "Item", "width": 160},
        {"fieldname": "final_price", "label": _("Final Price ₹"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 140},
        {"fieldname": "waiting_min", "label": _("Waiting (min)"), "fieldtype": "Float", "width": 110},
    ]


def get_data(filters):
    rows = frappe.db.sql("""
        SELECT
            name, store, customer_name, item, final_price, status,
            ROUND(TIMESTAMPDIFF(MINUTE, modified, NOW()), 1) as waiting_min
        FROM `tabBuyback Order`
        WHERE docstatus < 2
            AND status IN ('Awaiting Customer Approval', 'Awaiting Approval', 'Awaiting OTP')
        ORDER BY modified ASC
        LIMIT 500
    """, as_dict=1)
    return rows
