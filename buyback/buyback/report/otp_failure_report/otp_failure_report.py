# Copyright (c) 2026, GoStack and contributors
# R11 — OTP / Approval Failure Report
# Failed OTP attempts, expired links, branch / user wise.

import frappe
from frappe import _
from buyback.buyback.report.report_utils import date_condition, standard_conditions


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"fieldname": "name", "label": _("OTP Log"), "fieldtype": "Link", "options": "CH OTP Log", "width": 160},
        {"fieldname": "mobile_no", "label": _("Mobile"), "fieldtype": "Data", "width": 120},
        {"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 100},
        {"fieldname": "store", "label": _("Branch"), "fieldtype": "Link", "options": "Warehouse", "width": 160},
        {"fieldname": "creation", "label": _("Time"), "fieldtype": "Datetime", "width": 160},
        {"fieldname": "attempts", "label": _("Attempts"), "fieldtype": "Int", "width": 80},
        {"fieldname": "order_name", "label": _("Order"), "fieldtype": "Link", "options": "Buyback Order", "width": 160},
    ]


def get_data(filters):
    dc = date_condition("l.creation", filters)
    # OTP logs linked to buyback orders
    rows = frappe.db.sql(f"""
        SELECT
            l.name, l.mobile_no, l.status, l.creation,
            IFNULL(l.attempts, 1) as attempts,
            o.name as order_name, o.store
        FROM `tabCH OTP Log` l
        LEFT JOIN `tabBuyback Order` o ON o.mobile_no = l.mobile_no
            AND o.creation BETWEEN DATE_SUB(l.creation, INTERVAL 2 HOUR) AND l.creation
        WHERE l.status IN ('Failed','Expired','Pending')
            AND {dc}
        ORDER BY l.creation DESC
        LIMIT 500
    """, as_dict=1)
    return rows
