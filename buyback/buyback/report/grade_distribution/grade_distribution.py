# Copyright (c) 2026, GoStack and contributors
# R8 — Grade Variance / Distribution
# Estimated grade vs final inspector grade by branch / model / inspector.

import frappe
from frappe import _
from buyback.buyback.report.report_utils import date_condition, standard_conditions


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    chart = get_chart(data)
    return columns, data, None, chart


def get_columns():
    return [
        {"fieldname": "store", "label": _("Branch"), "fieldtype": "Link", "options": "Warehouse", "width": 160},
        {"fieldname": "item", "label": _("Model"), "fieldtype": "Link", "options": "Item", "width": 160},
        {"fieldname": "inspector", "label": _("Inspector"), "fieldtype": "Link", "options": "User", "width": 160},
        {"fieldname": "estimated_grade", "label": _("Estimated Grade"), "fieldtype": "Data", "width": 130},
        {"fieldname": "final_grade", "label": _("Final Grade"), "fieldtype": "Data", "width": 110},
        {"fieldname": "grade_changed", "label": _("Changed?"), "fieldtype": "Check", "width": 80},
        {"fieldname": "price_impact", "label": _("Price Impact ₹"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "order_name", "label": _("Order"), "fieldtype": "Link", "options": "Buyback Order", "width": 150},
    ]


def get_data(filters):
    dc = date_condition("o.creation", filters)
    sc = standard_conditions(filters, alias="o.")
    rows = frappe.db.sql(f"""
        SELECT
            o.store, o.item, i.inspector,
            a.estimated_grade,
            o.condition_grade as final_grade,
            CASE WHEN a.estimated_grade IS NOT NULL
                 AND a.estimated_grade != '' AND a.estimated_grade != o.condition_grade
                 THEN 1 ELSE 0 END as grade_changed,
            COALESCE(o.final_price,0) - COALESCE(a.estimated_price,0) as price_impact,
            o.name as order_name
        FROM `tabBuyback Order` o
        LEFT JOIN `tabBuyback Assessment` a ON a.name = o.buyback_assessment
        LEFT JOIN `tabBuyback Inspection` i ON i.name = o.buyback_inspection
        WHERE o.docstatus < 2 AND {dc} {sc}
        ORDER BY grade_changed DESC, ABS(COALESCE(o.final_price,0) - COALESCE(a.estimated_price,0)) DESC
    """, as_dict=1)
    return rows


def get_chart(data):
    if not data:
        return None
    changed = sum(1 for d in data if d.get("grade_changed"))
    unchanged = len(data) - changed
    return {
        "data": {
            "labels": [_("Grade Unchanged"), _("Grade Changed")],
            "datasets": [{"values": [unchanged, changed]}],
        },
        "type": "donut",
        "colors": ["#36a2eb", "#ff6384"],
    }
