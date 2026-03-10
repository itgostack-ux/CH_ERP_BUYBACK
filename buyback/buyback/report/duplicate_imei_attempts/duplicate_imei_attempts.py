# Copyright (c) 2026, GoStack and contributors
# R15 — Duplicate IMEI / Repeated Diagnosis
# Same IMEI appearing in multiple assessments.

import frappe
from frappe import _
from buyback.buyback.report.report_utils import date_condition


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"fieldname": "imei_serial", "label": _("IMEI / Serial"), "fieldtype": "Data", "width": 180},
        {"fieldname": "attempt_count", "label": _("Attempts"), "fieldtype": "Int", "width": 90},
        {"fieldname": "assessment_count", "label": _("Assessments"), "fieldtype": "Int", "width": 100},
        {"fieldname": "latest_store", "label": _("Latest Branch"), "fieldtype": "Link", "options": "Warehouse", "width": 160},
        {"fieldname": "latest_status", "label": _("Latest Status"), "fieldtype": "Data", "width": 120},
        {"fieldname": "latest_date", "label": _("Latest Date"), "fieldtype": "Datetime", "width": 160},
    ]


def get_data(filters):
    dc = date_condition("creation", filters)

    # Combine assessments for same IMEI
    rows = frappe.db.sql(f"""
        SELECT
            imei_serial,
            COUNT(*) as attempt_count,
            COUNT(*) as assessment_count,
            MAX(store) as latest_store,
            MAX(status) as latest_status,
            MAX(creation) as latest_date
        FROM `tabBuyback Assessment`
        WHERE imei_serial IS NOT NULL AND imei_serial != '' AND {dc}
        GROUP BY imei_serial
        HAVING attempt_count > 1
        ORDER BY attempt_count DESC
        LIMIT 500
    """, as_dict=1)
    return rows
