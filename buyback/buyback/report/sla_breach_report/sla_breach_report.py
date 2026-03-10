# Copyright (c) 2026, GoStack and contributors
# R17 — SLA Breach Report
# All SLA breaches by stage, with document link and time exceeded.

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
        {"fieldname": "name", "label": _("SLA Log"), "fieldtype": "Link", "options": "Buyback SLA Log", "width": 150},
        {"fieldname": "sla_stage", "label": _("Stage"), "fieldtype": "Data", "width": 180},
        {"fieldname": "reference_name", "label": _("Document"), "fieldtype": "Dynamic Link", "options": "reference_doctype", "width": 160},
        {"fieldname": "store", "label": _("Branch"), "fieldtype": "Link", "options": "Warehouse", "width": 150},
        {"fieldname": "expected_minutes", "label": _("SLA (min)"), "fieldtype": "Float", "width": 90},
        {"fieldname": "actual_minutes", "label": _("Actual (min)"), "fieldtype": "Float", "width": 100},
        {"fieldname": "exceeded_by", "label": _("Exceeded By (min)"), "fieldtype": "Float", "width": 130},
        {"fieldname": "creation", "label": _("Time"), "fieldtype": "Datetime", "width": 150},
    ]


def get_data(filters):
    dc = date_condition("s.creation", filters)
    sc = standard_conditions(filters, alias="s.")
    sla_stage_filter = ""
    if filters and filters.get("sla_stage"):
        sla_stage_filter = f" AND s.sla_stage = {frappe.db.escape(filters['sla_stage'])}"

    rows = frappe.db.sql(f"""
        SELECT
            s.name, s.sla_stage, s.reference_doctype, s.reference_name,
            s.store,
            s.expected_minutes,
            s.actual_minutes,
            ROUND(s.actual_minutes - s.expected_minutes, 1) as exceeded_by,
            s.creation
        FROM `tabBuyback SLA Log` s
        WHERE s.breached = 1
            AND {dc} {sc} {sla_stage_filter}
        ORDER BY exceeded_by DESC
        LIMIT 500
    """, as_dict=1)
    return rows


def get_chart(data):
    if not data:
        return None
    stage_counts = {}
    for d in data:
        stage = d.get("sla_stage", "Unknown")
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    labels = sorted(stage_counts.keys())
    return {
        "data": {
            "labels": labels,
            "datasets": [{"name": _("Breaches"), "values": [stage_counts[l] for l in labels]}],
        },
        "type": "bar",
        "colors": ["#ff4560"],
    }
