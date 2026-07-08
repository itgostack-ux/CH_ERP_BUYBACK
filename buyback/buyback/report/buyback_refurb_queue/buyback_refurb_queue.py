# Copyright (c) 2026, Congruence Holdings and contributors
# For license information, please see license.txt
"""Buyback Refurb Queue — orders that finished the buyback lifecycle but
haven't been picked up on the refurbishment side yet.

Market-standard parity: Cashify's back-office and Samsung's Trade-In portal
both show a "Ready for Refurb" queue so the refurb team never sits idle.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint


def execute(filters: dict | None = None) -> tuple[list[dict], list[dict]]:
    filters = filters or {}
    return _columns(), _rows(filters)


def _columns() -> list[dict]:
    return [
        {
            "label": _("Buyback Order"),
            "fieldname": "buyback_order",
            "fieldtype": "Link",
            "options": "Buyback Order",
            "width": 160,
        },
        {
            "label": _("Status"),
            "fieldname": "status",
            "fieldtype": "Data",
            "width": 90,
        },
        {
            "label": _("Days Since Payout"),
            "fieldname": "days_since_payout",
            "fieldtype": "Int",
            "width": 130,
        },
        {
            "label": _("Item"),
            "fieldname": "item",
            "fieldtype": "Link",
            "options": "Item",
            "width": 170,
        },
        {
            "label": _("IMEI / Serial"),
            "fieldname": "imei_serial",
            "fieldtype": "Data",
            "width": 150,
        },
        {
            "label": _("Grade"),
            "fieldname": "condition_grade",
            "fieldtype": "Link",
            "options": "Grade Master",
            "width": 90,
        },
        {
            "label": _("Customer"),
            "fieldname": "customer_name",
            "fieldtype": "Data",
            "width": 170,
        },
        {
            "label": _("Store"),
            "fieldname": "store",
            "fieldtype": "Link",
            "options": "Warehouse",
            "width": 150,
        },
        {
            "label": _("Indemnity"),
            "fieldname": "has_indemnity",
            "fieldtype": "Check",
            "width": 90,
        },
        {
            "label": _("Data Wipe"),
            "fieldname": "has_data_wipe",
            "fieldtype": "Check",
            "width": 90,
        },
        {
            "label": _("Data Wipe Cert."),
            "fieldname": "data_wipe_certificate",
            "fieldtype": "Link",
            "options": "CH Data Wipe Certificate",
            "width": 150,
        },
        {
            "label": _("Refurbishment Order"),
            "fieldname": "linked_refurbishment",
            "fieldtype": "Link",
            "options": "Refurbishment Order",
            "width": 170,
        },
        {
            "label": _("Final Price"),
            "fieldname": "final_price",
            "fieldtype": "Currency",
            "width": 110,
        },
    ]


def _rows(filters: dict[str, Any]) -> list[dict]:
    min_age = cint(filters.get("min_age_days") or 0)
    only_missing_wipe = cint(filters.get("only_missing_wipe") or 0)
    company = filters.get("company")

    conditions = ["bo.status IN ('Paid', 'Closed')", "bo.docstatus = 1"]
    values: dict[str, Any] = {}
    if company:
        conditions.append("bo.company = %(company)s")
        values["company"] = company
    if min_age:
        conditions.append(
            "DATEDIFF(CURDATE(), bo.modified) >= %(min_age)s"
        )
        values["min_age"] = min_age

    where = " AND ".join(conditions)

    rows = frappe.db.sql(
        f"""
        SELECT
            bo.name                       AS buyback_order,
            bo.status                     AS status,
            bo.item                       AS item,
            bo.imei_serial                AS imei_serial,
            bo.condition_grade            AS condition_grade,
            bo.customer_name              AS customer_name,
            bo.store                      AS store,
            bo.final_price                AS final_price,
            bo.data_wipe_certificate      AS data_wipe_certificate,
            IFNULL(bo.indemnity_signed, 0)   AS has_indemnity,
            CASE
                WHEN bo.data_wipe_certificate IS NOT NULL
                    AND bo.data_wipe_certificate != '' THEN 1
                ELSE 0
            END                            AS has_data_wipe,
            DATEDIFF(
                CURDATE(),
                bo.modified
            )                              AS days_since_payout,
            (
                SELECT ro.name
                FROM `tabRefurbishment Order` ro
                WHERE ro.docstatus < 2
                  AND ro.serial_no IS NOT NULL
                  AND ro.serial_no != ''
                  AND ro.serial_no = bo.imei_serial
                ORDER BY ro.creation DESC
                LIMIT 1
            )                              AS linked_refurbishment
        FROM `tabBuyback Order` bo
        WHERE {where}
        ORDER BY days_since_payout DESC, bo.modified DESC
        """,
        values,
        as_dict=True,
    ) or []

    # Post-filter: prune orders that already have a Refurbishment Order
    # AND already have a wipe certificate — nothing left to chase.
    result = []
    for r in rows:
        has_wipe = bool(r.get("has_data_wipe"))
        if only_missing_wipe and has_wipe:
            continue
        # Always keep orders missing wipe OR missing refurb order.
        if r.get("linked_refurbishment") and has_wipe:
            continue
        result.append(r)
    return result
