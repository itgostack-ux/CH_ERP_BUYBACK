"""Rename the Buyback Assessment/Inspection ``source`` option "Web" to "App".

The Select option itself was renamed in both doctype JSONs; this backfills
rows written before the rename so history and filters stay consistent.
"""

from __future__ import annotations

import frappe

DOCTYPES = ("Buyback Assessment", "Buyback Inspection")


def execute():
    for doctype in DOCTYPES:
        if not frappe.db.table_exists(doctype):
            continue
        frappe.db.sql(
            f"""
            UPDATE `tab{doctype}`
            SET source = 'App'
            WHERE source = 'Web'
            """
        )
