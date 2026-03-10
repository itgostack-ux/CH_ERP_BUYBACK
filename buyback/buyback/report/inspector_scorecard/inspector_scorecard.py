# Copyright (c) 2026, GoStack and contributors
# R5 — Inspector Performance
# Per-inspector: inspections done, mismatch %, final variance %, SLA compliance.

import frappe
from frappe import _
from frappe.utils import flt
from buyback.buyback.report.report_utils import date_condition, standard_conditions


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    chart = get_chart(data)
    return columns, data, None, chart


def get_columns():
    return [
        {"fieldname": "inspector", "label": _("Inspector"), "fieldtype": "Link", "options": "User", "width": 200},
        {"fieldname": "inspections", "label": _("Inspections"), "fieldtype": "Int", "width": 100},
        {"fieldname": "avg_mismatch_pct", "label": _("Avg Mismatch %"), "fieldtype": "Percent", "width": 130},
        {"fieldname": "avg_variance_pct", "label": _("Avg Price Variance %"), "fieldtype": "Percent", "width": 150},
        {"fieldname": "rejection_pct", "label": _("Rejection %"), "fieldtype": "Percent", "width": 110},
        {"fieldname": "avg_duration_min", "label": _("Avg Duration (min)"), "fieldtype": "Float", "width": 140},
        {"fieldname": "sla_compliance_pct", "label": _("SLA Compliance %"), "fieldtype": "Percent", "width": 140},
    ]


def get_data(filters):
    dc = date_condition("i.creation", filters)
    sc = standard_conditions(filters, alias="i.")

    rows = frappe.db.sql(f"""
        SELECT
            i.inspector,
            COUNT(*) as inspections,
            ROUND(AVG(IFNULL(i.mismatch_percentage, 0)), 1) as avg_mismatch_pct,
            ROUND(AVG(IFNULL(i.price_variance_from_comparison, 0)), 1) as avg_variance_pct,
            ROUND(SUM(CASE WHEN i.status='Rejected' THEN 1 ELSE 0 END) / COUNT(*) * 100, 1) as rejection_pct,
            ROUND(AVG(TIMESTAMPDIFF(MINUTE, i.creation, IFNULL(i.inspection_completed_at, i.modified))), 1) as avg_duration_min,
            ROUND(SUM(CASE WHEN TIMESTAMPDIFF(MINUTE, i.creation, IFNULL(i.inspection_completed_at, i.modified)) <= 30 THEN 1 ELSE 0 END)
                  / COUNT(*) * 100, 1) as sla_compliance_pct
        FROM `tabBuyback Inspection` i
        WHERE i.status IN ('Completed','Rejected') AND i.inspector IS NOT NULL
            AND {dc} {sc}
        GROUP BY i.inspector
        ORDER BY inspections DESC
    """, as_dict=1)
    return rows


def get_chart(data):
    if not data:
        return None
    top = data[:10]
    return {
        "data": {
            "labels": [d["inspector"] for d in top],
            "datasets": [
                {"name": _("Inspections"), "values": [d["inspections"] for d in top]},
                {"name": _("Mismatch %"), "values": [d["avg_mismatch_pct"] for d in top]},
            ],
        },
        "type": "bar",
    }
