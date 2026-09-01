"""Backfill the IMEI blacklist from CEIR verdicts that were only ever recorded locally.

`submit_imei_validation` on both Buyback Assessment and Buyback Order flagged a
Sanchar Saathi hit on the document and stopped there — nothing in the codebase
ever wrote a `Buyback IMEI Blacklist` row, so `check_imei_and_block` had nothing
to find and the same handset could be re-assessed at another store the next day.
Now that both paths record the block, carry the existing verdicts across so the
blacklist reflects every device already rejected.
"""

from __future__ import annotations

import frappe

from buyback.buyback.doctype.buyback_imei_blacklist.buyback_imei_blacklist import (
    CEIR_BLOCKING_STATUSES,
    record_ceir_block,
)

SOURCES = ("Buyback Assessment", "Buyback Order")


def execute():
    if not frappe.db.table_exists("Buyback IMEI Blacklist"):
        return

    recorded = 0
    for doctype in SOURCES:
        if not frappe.db.table_exists(doctype):
            continue
        if not frappe.db.has_column(doctype, "imei_validation_status"):
            continue
        rows = frappe.get_all(
            doctype,
            filters={
                "imei_validation_status": ["in", CEIR_BLOCKING_STATUSES],
                "imei_serial": ["is", "set"],
            },
            fields=["name", "imei_serial", "imei_validation_status",
                    "imei_validation_remarks"],
            limit_page_length=0,
        )
        for row in rows:
            if record_ceir_block(
                row.imei_serial,
                row.imei_validation_status,
                reference_doctype=doctype,
                reference_name=row.name,
                remarks=row.imei_validation_remarks,
            ):
                recorded += 1

    if recorded:
        frappe.logger("buyback").info(
            f"Backfilled {recorded} CEIR-flagged IMEIs onto Buyback IMEI Blacklist"
        )
