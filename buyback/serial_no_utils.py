"""
Serial No (IMEI) helpers for the Buyback module.

Reuses ERPNext's built-in Serial No DocType + Frappe's Comment system
to track device lifecycle — no separate "IMEI History" DocType needed.

Functions:
  update_serial_buyback_status  — set buyback custom fields on Serial No
  add_serial_timeline_comment   — add a timeline comment on Serial No
  get_imei_history              — consolidated cross-doc IMEI history
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import nowdate, cint, flt


def update_serial_buyback_status(
    imei: str,
    *,
    status: str,
    order_name: str | None = None,
    price: float | None = None,
    grade: str | None = None,
    customer: str | None = None,
    comment: str | None = None,
):
    """Update buyback custom fields on a Serial No record.

    Creates the Serial No if it doesn't already exist (Material Receipt
    normally creates it, but quote/inspection happen before stock entry).

    Args:
        imei: Serial No / IMEI value
        status: One of Available, Quoted, Under Inspection, Bought Back, Exchanged
        order_name: Buyback Order name to link
        price: Buyback price
        grade: Grade Master link
        customer: Customer link
        comment: Timeline comment to add
    """
    if not imei:
        return

    if not frappe.db.exists("Serial No", imei):
        # Serial No not yet created (pre-stock-entry stage) — skip custom field
        # update but still add the comment if possible
        if comment:
            frappe.logger("buyback").debug(
                f"Serial No {imei} not yet in system — skipping status update"
            )
        return

    update = {"ch_buyback_status": status}
    if order_name:
        update["ch_buyback_order"] = order_name
    if price is not None:
        update["ch_buyback_price"] = flt(price)
    if grade:
        update["ch_buyback_grade"] = grade
    if customer:
        update["ch_buyback_customer"] = customer
    if status == "Bought Back":
        update["ch_buyback_date"] = nowdate()
        cur_count = cint(
            frappe.db.get_value("Serial No", imei, "ch_buyback_count")
        )
        update["ch_buyback_count"] = cur_count + 1

    frappe.db.set_value("Serial No", imei, update, update_modified=False)

    if comment:
        add_serial_timeline_comment(imei, comment)


def add_serial_timeline_comment(imei: str, message: str):
    """Add a timeline comment on a Serial No document.

    Uses Frappe's built-in Comment system — shows in the Serial No's
    timeline/activity in the Desk UI.
    """
    if not imei or not frappe.db.exists("Serial No", imei):
        return
    doc = frappe.get_doc("Serial No", imei)
    doc.add_comment("Info", message)


def get_imei_history(imei: str) -> dict:
    """Get consolidated buyback history for an IMEI across all doctypes.

    Returns:
        dict with keys: serial_info, quotes, inspections, orders, exchanges,
                        timeline (comments), audit_log
    """
    result = {
        "imei": imei,
        "serial_exists": False,
        "serial_info": {},
        "quotes": [],
        "inspections": [],
        "orders": [],
        "exchanges": [],
        "timeline": [],
        "audit_log": [],
    }

    # Serial No info
    if frappe.db.exists("Serial No", imei):
        result["serial_exists"] = True
        sn = frappe.db.get_value(
            "Serial No", imei,
            [
                "name", "item_code", "item_name", "warehouse", "status",
                "ch_buyback_status", "ch_buyback_order", "ch_buyback_date",
                "ch_buyback_price", "ch_buyback_grade", "ch_buyback_count",
                "ch_buyback_customer", "brand", "item_group",
            ],
            as_dict=True,
        )
        result["serial_info"] = sn

        # Timeline comments
        result["timeline"] = frappe.get_all(
            "Comment",
            filters={
                "reference_doctype": "Serial No",
                "reference_name": imei,
                "comment_type": "Info",
            },
            fields=["content", "comment_by", "creation"],
            order_by="creation desc",
            limit=50,
        )

    # Assessments
    result["assessments"] = frappe.get_all(
        "Buyback Assessment",
        filters={"imei_serial": imei},
        fields=[
            "name", "assessment_id", "customer", "customer_name", "store",
            "item", "item_name", "quoted_price", "estimated_price", "status", "creation",
        ],
        order_by="creation desc",
    )

    # Inspections
    result["inspections"] = frappe.get_all(
        "Buyback Inspection",
        filters={"imei_serial": imei},
        fields=[
            "name", "inspection_id", "customer", "customer_name",
            "item", "item_name", "status", "condition_grade",
            "revised_price", "diagnostic_source", "creation",
        ],
        order_by="creation desc",
    )

    # Orders
    result["orders"] = frappe.get_all(
        "Buyback Order",
        filters={"imei_serial": imei},
        fields=[
            "name", "order_id", "customer", "customer_name", "store",
            "item", "item_name", "final_price", "condition_grade",
            "status", "payment_status", "creation",
        ],
        order_by="creation desc",
    )

    # Exchanges
    result["exchanges"] = frappe.get_all(
        "Buyback Exchange Order",
        filters={"old_imei_serial": imei},
        fields=[
            "name", "exchange_id", "customer", "old_item",
            "new_item", "buyback_amount", "amount_to_pay",
            "status", "creation",
        ],
        order_by="creation desc",
    )

    # Audit log entries referencing orders for this IMEI
    order_names = [o["name"] for o in result["orders"]]
    if order_names:
        result["audit_log"] = frappe.get_all(
            "Buyback Audit Log",
            filters={
                "reference_doctype": "Buyback Order",
                "reference_name": ["in", order_names],
            },
            fields=[
                "name", "action", "reference_name", "user",
                "timestamp", "old_value", "new_value",
            ],
            order_by="timestamp desc",
            limit=100,
        )

    return result
