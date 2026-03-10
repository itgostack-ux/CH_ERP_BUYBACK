# Copyright (c) 2026, GoStack and contributors
# Pending Payments — Approved/Confirmed but unpaid orders.

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
        {"fieldname": "final_price", "label": _("Amount ₹"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "settlement_type", "label": _("Settlement"), "fieldtype": "Data", "width": 100},
        {"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 120},
        {"fieldname": "pending_min", "label": _("Pending (min)"), "fieldtype": "Float", "width": 110},
    ]


def get_data(filters):
    sc_parts = []
    if filters and filters.get("store"):
        sc_parts.append(f"store = {frappe.db.escape(filters['store'])}")
    sc = (" AND " + " AND ".join(sc_parts)) if sc_parts else ""

    rows = frappe.db.sql(f"""
        SELECT
            name, store, customer_name, final_price,
            IFNULL(settlement_type, 'Buyback') as settlement_type,
            status,
            ROUND(TIMESTAMPDIFF(MINUTE, modified, NOW()), 1) as pending_min
        FROM `tabBuyback Order`
        WHERE docstatus < 2
            AND status IN ('Customer Approved','Approved','Awaiting OTP')
            AND (total_paid IS NULL OR total_paid = 0)
            {sc}
        ORDER BY modified ASC
        LIMIT 500
    """, as_dict=1)
    return rows
