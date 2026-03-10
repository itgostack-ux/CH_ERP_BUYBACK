# Copyright (c) 2026, GoStack and contributors
# Category Trend — Weekly inflow by category / brand.

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
        {"fieldname": "item_group", "label": _("Category"), "fieldtype": "Link", "options": "Item Group", "width": 160},
        {"fieldname": "brand", "label": _("Brand"), "fieldtype": "Link", "options": "Brand", "width": 120},
        {"fieldname": "assessment_count", "label": _("Assessments"), "fieldtype": "Int", "width": 90},
        {"fieldname": "order_count", "label": _("Orders"), "fieldtype": "Int", "width": 90},
        {"fieldname": "total_value", "label": _("Total Value ₹"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "avg_price", "label": _("Avg Price ₹"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "app_pct", "label": _("App Source %"), "fieldtype": "Percent", "width": 110},
    ]


def get_data(filters):
    dc = date_condition("creation", filters)
    sc = standard_conditions(filters)
    rows = frappe.db.sql(f"""
        SELECT
            item_group, brand,
            COUNT(*) as assessment_count,
            0 as order_count,
            COALESCE(SUM(IFNULL(quoted_price, estimated_price)),0) as total_value,
            ROUND(AVG(IFNULL(quoted_price, estimated_price)),0) as avg_price,
            ROUND(SUM(CASE WHEN source='App Diagnosis' THEN 1 ELSE 0 END)/ COUNT(*) * 100, 1) as app_pct
        FROM `tabBuyback Assessment`
        WHERE {dc} {sc}
        GROUP BY item_group, brand
        ORDER BY assessment_count DESC
    """, as_dict=1)
    return rows


def get_chart(data):
    if not data:
        return None
    top = data[:10]
    return {
        "data": {
            "labels": [f"{d.get('brand','')} / {d.get('item_group','')}" for d in top],
            "datasets": [{"name": _("Assessments"), "values": [d.get("assessment_count",0) for d in top]}],
        },
        "type": "bar",
        "colors": ["#ffa00a"],
    }
