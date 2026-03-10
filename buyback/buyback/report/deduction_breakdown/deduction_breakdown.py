# Copyright (c) 2026, GoStack and contributors
# Deduction Breakdown — Analysis of price deductions by inspection findings.

import frappe
from frappe import _
from buyback.buyback.report.report_utils import date_condition, standard_conditions


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"fieldname": "check_code", "label": _("Check / Question"), "fieldtype": "Data", "width": 200},
        {"fieldname": "times_flagged", "label": _("Times Flagged"), "fieldtype": "Int", "width": 110},
        {"fieldname": "avg_deduction", "label": _("Avg Deduction %"), "fieldtype": "Percent", "width": 130},
        {"fieldname": "total_impact", "label": _("Total Impact ₹"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "mismatch_count", "label": _("Mismatches"), "fieldtype": "Int", "width": 100},
    ]


def get_data(filters):
    dc = date_condition("i.creation", filters)
    sc = standard_conditions(filters, alias="i.")

    rows = frappe.db.sql(f"""
        SELECT
            c.question_code as check_code,
            COUNT(*) as times_flagged,
            ROUND(AVG(ABS(IFNULL(c.price_impact_difference, 0))), 1) as avg_deduction,
            COALESCE(SUM(ABS(IFNULL(c.price_impact_difference, 0))), 0) as total_impact,
            SUM(CASE WHEN c.match_status = 'Mismatch' THEN 1 ELSE 0 END) as mismatch_count
        FROM `tabBuyback Inspection Comparison` c
        JOIN `tabBuyback Inspection` i ON i.name = c.parent
        WHERE i.status = 'Completed' AND {dc} {sc}
        GROUP BY c.question_code
        ORDER BY total_impact DESC
    """, as_dict=1)
    return rows
