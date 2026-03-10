# Copyright (c) 2026, GoStack and contributors
# Finance Payout Register — All payments and exchange adjustments.
# Finance-friendly with export support.

import frappe
from frappe import _
from buyback.buyback.report.report_utils import date_condition, standard_conditions


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    summary = get_summary(data)
    return columns, data, None, None, summary


def get_columns():
    return [
        {"fieldname": "order_name", "label": _("Order"), "fieldtype": "Link", "options": "Buyback Order", "width": 150},
        {"fieldname": "store", "label": _("Branch"), "fieldtype": "Link", "options": "Warehouse", "width": 150},
        {"fieldname": "customer_name", "label": _("Customer"), "fieldtype": "Data", "width": 150},
        {"fieldname": "settlement_type", "label": _("Settlement"), "fieldtype": "Data", "width": 100},
        {"fieldname": "final_price", "label": _("Final Price ₹"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "total_paid", "label": _("Paid ₹"), "fieldtype": "Currency", "width": 110},
        {"fieldname": "payment_mode", "label": _("Mode"), "fieldtype": "Data", "width": 100},
        {"fieldname": "payment_date", "label": _("Payment Date"), "fieldtype": "Datetime", "width": 150},
        {"fieldname": "exchange_discount", "label": _("Exchange Adj ₹"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "balance_to_pay", "label": _("Balance Due ₹"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 100},
    ]


def get_data(filters):
    dc = date_condition("o.creation", filters)
    sc = standard_conditions(filters, alias="o.")

    rows = frappe.db.sql(f"""
        SELECT
            o.name as order_name, o.store, o.customer_name,
            IFNULL(o.settlement_type, 'Buyback') as settlement_type,
            o.final_price, o.total_paid,
            p.payment_method as payment_mode,
            p.payment_date,
            IFNULL(o.exchange_discount, 0) as exchange_discount,
            IFNULL(o.balance_to_pay, 0) as balance_to_pay,
            o.status
        FROM `tabBuyback Order` o
        LEFT JOIN `tabBuyback Order Payment` p
            ON p.parent = o.name AND p.parenttype = 'Buyback Order'
        WHERE o.docstatus < 2
            AND o.status IN ('Paid','Closed','Awaiting OTP','Customer Approved')
            AND {dc} {sc}
        ORDER BY p.payment_date DESC, o.creation DESC
    """, as_dict=1)
    return rows


def get_summary(data):
    if not data:
        return []
    total_paid = sum(d.get("total_paid",0) or 0 for d in data)
    total_exchange = sum(d.get("exchange_discount",0) or 0 for d in data)
    return [
        {"value": total_paid, "label": _("Total Paid"), "datatype": "Currency", "indicator": "green"},
        {"value": total_exchange, "label": _("Exchange Adjustments"), "datatype": "Currency", "indicator": "blue"},
        {"value": len(data), "label": _("Records"), "datatype": "Int"},
    ]
