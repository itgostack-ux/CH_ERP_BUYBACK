# Copyright (c) 2026, GoStack and contributors
# R6 — Model-wise Buyback Summary
# Volume and value by brand / model with avg price and settlement mix.

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
        {"fieldname": "item_group", "label": _("Category"), "fieldtype": "Link", "options": "Item Group", "width": 140},
        {"fieldname": "brand", "label": _("Brand"), "fieldtype": "Link", "options": "Brand", "width": 120},
        {"fieldname": "item", "label": _("Model"), "fieldtype": "Link", "options": "Item", "width": 180},
        {"fieldname": "quote_count", "label": _("Quotes"), "fieldtype": "Int", "width": 80},
        {"fieldname": "settled_count", "label": _("Settled"), "fieldtype": "Int", "width": 80},
        {"fieldname": "avg_final_price", "label": _("Avg Final ₹"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "total_value", "label": _("Total Value ₹"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "buyback_pct", "label": _("Buyback %"), "fieldtype": "Percent", "width": 100},
        {"fieldname": "exchange_pct", "label": _("Exchange %"), "fieldtype": "Percent", "width": 100},
        {"fieldname": "avg_variance_pct", "label": _("Avg Variance %"), "fieldtype": "Percent", "width": 120},
    ]


def get_data(filters):
    dc = date_condition("o.creation", filters)
    sc = standard_conditions(filters, alias="o.")
    rows = frappe.db.sql(f"""
        SELECT
            itm.item_group, o.brand, o.item,
            COUNT(*) as quote_count,
            SUM(CASE WHEN o.status IN ('Paid','Closed') THEN 1 ELSE 0 END) as settled_count,
            ROUND(AVG(o.final_price),0) as avg_final_price,
            COALESCE(SUM(o.final_price),0) as total_value,
            SUM(CASE WHEN o.settlement_type='Buyback' THEN 1 ELSE 0 END) as bb_count,
            SUM(CASE WHEN o.settlement_type='Exchange' THEN 1 ELSE 0 END) as ex_count,
            ROUND(AVG(o.price_variance_pct),1) as avg_variance_pct
        FROM `tabBuyback Order` o
        LEFT JOIN `tabItem` itm ON itm.name = o.item
        WHERE o.docstatus < 2 AND {dc} {sc}
        GROUP BY itm.item_group, o.brand, o.item
        ORDER BY total_value DESC
    """, as_dict=1)

    for r in rows:
        total = (r.bb_count or 0) + (r.ex_count or 0)
        r["buyback_pct"] = round((r.bb_count or 0) / total * 100, 1) if total else 0
        r["exchange_pct"] = round((r.ex_count or 0) / total * 100, 1) if total else 0
        r.pop("bb_count", None)
        r.pop("ex_count", None)
    return rows


def get_chart(data):
    if not data:
        return None
    top = data[:10]
    return {
        "data": {
            "labels": [d.get("item", "") for d in top],
            "datasets": [{"name": _("Total Value"), "values": [d.get("total_value",0) for d in top]}],
        },
        "type": "bar",
        "colors": ["#7575ff"],
    }
