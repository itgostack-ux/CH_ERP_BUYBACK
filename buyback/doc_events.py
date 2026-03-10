"""
Document event hooks for the Buyback app.

Registered in hooks.py doc_events — these fire on standard Frappe
document lifecycle events for buyback-related doctypes.

Purpose: Update Serial No (IMEI) buyback status and timeline at each
stage of the buyback flow — Assessment → Inspection → Order → Close.
"""

import frappe
from buyback.serial_no_utils import update_serial_buyback_status


def on_assessment_created(doc, method=None):
    """After a Buyback Assessment is inserted, mark the IMEI as 'Assessment Created'."""
    if doc.imei_serial:
        update_serial_buyback_status(
            doc.imei_serial,
            status="Quoted",
            comment=f"📱 Self-assessment {doc.name} created via {doc.source} — est. ₹{doc.estimated_price}",
        )


def on_inspection_update(doc, method=None):
    """When a Buyback Inspection is completed, update IMEI status."""
    if not doc.imei_serial:
        return

    if doc.status == "In Progress":
        update_serial_buyback_status(
            doc.imei_serial,
            status="Under Inspection",
            comment=f"🔍 Physical inspection started — {doc.name}",
        )
    elif doc.status == "Completed":
        grade_name = ""
        if doc.condition_grade:
            grade_name = frappe.db.get_value(
                "Grade Master", doc.condition_grade, "grade_name"
            ) or doc.condition_grade
        update_serial_buyback_status(
            doc.imei_serial,
            status="Under Inspection",
            grade=doc.condition_grade,
            comment=(
                f"✅ Inspection completed — {doc.name}, "
                f"Grade: {grade_name}, "
                f"Revised Price: ₹{doc.revised_price or 'N/A'}"
            ),
        )
    elif doc.status == "Rejected":
        update_serial_buyback_status(
            doc.imei_serial,
            status="Available",
            comment=f"❌ Device rejected in inspection {doc.name}",
        )
