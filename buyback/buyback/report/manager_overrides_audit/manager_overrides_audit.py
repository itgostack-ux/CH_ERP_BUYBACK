# Copyright (c) 2026, GoStack and contributors
# R14 — Overrides & Approval Audit
# All manual price overrides and manager approvals.

import frappe
from frappe import _
from buyback.buyback.report.report_utils import date_condition, standard_conditions


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"fieldname": "name", "label": _("Audit Log"), "fieldtype": "Link", "options": "Buyback Audit Log", "width": 160},
        {"fieldname": "action", "label": _("Action"), "fieldtype": "Data", "width": 160},
        {"fieldname": "reference_name", "label": _("Order"), "fieldtype": "Dynamic Link", "options": "reference_doctype", "width": 160},
        {"fieldname": "store", "label": _("Branch"), "fieldtype": "Link", "options": "Warehouse", "width": 160},
        {"fieldname": "performed_by", "label": _("Performed By"), "fieldtype": "Link", "options": "User", "width": 180},
        {"fieldname": "old_value", "label": _("Old Value"), "fieldtype": "Data", "width": 120},
        {"fieldname": "new_value", "label": _("New Value"), "fieldtype": "Data", "width": 120},
        {"fieldname": "creation", "label": _("Time"), "fieldtype": "Datetime", "width": 160},
    ]


def get_data(filters):
    dc = date_condition("a.creation", filters)
    sc = standard_conditions(filters, alias="a.")
    rows = frappe.db.sql(f"""
        SELECT
            a.name, a.action,
            a.reference_doctype, a.reference_name,
            a.owner as performed_by,
            a.old_value, a.new_value,
            a.creation,
            o.store
        FROM `tabBuyback Audit Log` a
        LEFT JOIN `tabBuyback Order` o ON o.name = a.reference_name
            AND a.reference_doctype = 'Buyback Order'
        WHERE a.action IN ('Price Override','Grade Changed','Order Approved','Order Rejected',
                           'Settlement Type Changed','Customer Approved')
            AND {dc} {sc}
        ORDER BY a.creation DESC
        LIMIT 500
    """, as_dict=1)
    return rows
