# Copyright (c) 2026, GoStack and contributors
# R7 — Price Variance
# Estimated vs final price, broken by model/branch/source/inspector.

import frappe
from frappe import _
from frappe.utils import flt
from buyback.buyback.report.report_utils import date_condition, standard_conditions


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"fieldname": "name", "label": _("Order"), "fieldtype": "Link", "options": "Buyback Order", "width": 150},
        {"fieldname": "store", "label": _("Branch"), "fieldtype": "Link", "options": "Warehouse", "width": 150},
        {"fieldname": "item", "label": _("Model"), "fieldtype": "Link", "options": "Item", "width": 160},
        {"fieldname": "source", "label": _("Source"), "fieldtype": "Data", "width": 110},
        {"fieldname": "estimated_price", "label": _("Estimated ₹"), "fieldtype": "Currency", "width": 110},
        {"fieldname": "final_price", "label": _("Final ₹"), "fieldtype": "Currency", "width": 110},
        {"fieldname": "variance", "label": _("Variance ₹"), "fieldtype": "Currency", "width": 110},
        {"fieldname": "variance_pct", "label": _("Variance %"), "fieldtype": "Percent", "width": 110},
        {"fieldname": "inspector", "label": _("Inspector"), "fieldtype": "Link", "options": "User", "width": 160},
        {"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 100},
    ]


def get_data(filters):
    dc = date_condition("o.creation", filters)
    sc = standard_conditions(filters, alias="o.")
    threshold = flt((filters or {}).get("variance_threshold") or 10)

    rows = frappe.db.sql(f"""
        SELECT
            o.name, o.store, o.item,
            IFNULL(a.source, 'Store Manual') as source,
            COALESCE(o.original_quoted_price, a.quoted_price, a.estimated_price) as estimated_price,
            o.final_price,
            o.final_price - COALESCE(o.original_quoted_price, a.quoted_price, a.estimated_price) as variance,
            CASE WHEN COALESCE(o.original_quoted_price, a.quoted_price, a.estimated_price) > 0
                THEN ROUND((o.final_price - COALESCE(o.original_quoted_price, a.quoted_price, a.estimated_price))
                     / COALESCE(o.original_quoted_price, a.quoted_price, a.estimated_price) * 100, 1)
                ELSE 0 END as variance_pct,
            i.inspector,
            o.status
        FROM `tabBuyback Order` o
        LEFT JOIN `tabBuyback Assessment` a ON a.name = o.buyback_assessment
        LEFT JOIN `tabBuyback Inspection` i ON i.name = o.buyback_inspection
        WHERE o.docstatus < 2 AND o.final_price > 0
            AND {dc} {sc}
        HAVING ABS(variance_pct) >= {threshold}
        ORDER BY ABS(variance_pct) DESC
    """, as_dict=1)
    return rows
