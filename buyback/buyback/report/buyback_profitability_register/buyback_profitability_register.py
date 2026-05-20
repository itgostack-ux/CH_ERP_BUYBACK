# Copyright (c) 2026, GoStack and contributors
# Buyback Profitability Register
# Shows buyback amount vs linked sales invoice amount for margin visibility.

import frappe
from frappe import _

from buyback.buyback.report.report_utils import date_condition, standard_conditions


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"fieldname": "order_date", "label": _("Order Date"), "fieldtype": "Date", "width": 110},
        {"fieldname": "buyback_order", "label": _("Buyback Order"), "fieldtype": "Link", "options": "Buyback Order", "width": 170},
        {"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link", "options": "Customer", "width": 170},
        {"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "Warehouse", "width": 150},
        {"fieldname": "item", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 180},
        {"fieldname": "serial_no", "label": _("Serial No"), "fieldtype": "Data", "width": 150},
        {"fieldname": "settlement_type", "label": _("Settlement Type"), "fieldtype": "Data", "width": 120},
        {"fieldname": "buyback_amount", "label": _("Buyback Amount"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "sales_invoice", "label": _("Sales Invoice"), "fieldtype": "Link", "options": "Sales Invoice", "width": 160},
        {"fieldname": "sold_amount", "label": _("Sold Amount"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "gross_margin", "label": _("Gross Margin"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "margin_pct", "label": _("Margin %"), "fieldtype": "Percent", "width": 100},
        {"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 140},
    ]


def get_data(filters):
    filters = frappe._dict(filters or {})

    dc = date_condition("o.creation", filters)

    # item_group is on Item, not Buyback Order; filter it separately after join.
    item_group = filters.pop("item_group", None)
    sc = standard_conditions(filters, alias="o.")

    item_group_condition = ""
    if item_group:
        item_group_condition = f" AND i.item_group = {frappe.db.escape(item_group)}"

    sold_status = (filters.get("sold_status") or "All").strip()
    sold_condition = ""
    if sold_status == "Sold":
        sold_condition = " AND o.sales_invoice IS NOT NULL"
    elif sold_status == "Unsold":
        sold_condition = " AND o.sales_invoice IS NULL"

    rows = frappe.db.sql(
        """
        SELECT
            DATE(o.creation) AS order_date,
            o.name AS buyback_order,
            o.customer,
            o.store,
            o.item,
            o.serial_no,
            o.settlement_type,
            COALESCE(NULLIF(o.approved_price, 0), o.final_price, 0) AS buyback_amount,
            o.sales_invoice,
            CASE WHEN si.name IS NOT NULL THEN si.grand_total ELSE NULL END AS sold_amount,
            CASE
                WHEN si.name IS NOT NULL
                THEN si.grand_total - COALESCE(NULLIF(o.approved_price, 0), o.final_price, 0)
                ELSE NULL
            END AS gross_margin,
            CASE
                WHEN si.name IS NOT NULL AND COALESCE(NULLIF(o.approved_price, 0), o.final_price, 0) > 0
                THEN ROUND(
                    (
                        si.grand_total - COALESCE(NULLIF(o.approved_price, 0), o.final_price, 0)
                    ) / COALESCE(NULLIF(o.approved_price, 0), o.final_price, 0) * 100,
                    1
                )
                ELSE NULL
            END AS margin_pct,
            o.status
        FROM `tabBuyback Order` o
        LEFT JOIN `tabSales Invoice` si ON si.name = o.sales_invoice AND si.docstatus = 1
        LEFT JOIN `tabItem` i ON i.name = o.item
        WHERE o.docstatus < 2
            AND {dc} {sc} {item_group_condition} {sold_condition}
        ORDER BY o.creation DESC
        """.format(
            dc=dc,
            sc=sc,
            item_group_condition=item_group_condition,
            sold_condition=sold_condition,
        ),
        as_dict=1,
    )  # noqa: UP032

    return rows
