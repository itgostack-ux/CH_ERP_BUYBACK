"""Carry pre-order POS KYC evidence from assessments into their orders."""

import frappe
from frappe.utils import cint


def _latest_private_attachment(assessment_name: str, fieldname: str) -> str:
    return frappe.db.get_value(
        "File",
        {
            "attached_to_doctype": "Buyback Assessment",
            "attached_to_name": assessment_name,
            "attached_to_field": fieldname,
            "is_private": 1,
        },
        "file_url",
        order_by="creation desc",
    ) or ""


def execute():
    fields = (
        "kyc_id_type",
        "kyc_id_number",
        "customer_id_front",
        "customer_id_back",
        "customer_photo",
    )
    if any(not frappe.get_meta("Buyback Assessment").has_field(field) for field in fields):
        return

    for row in frappe.get_all(
        "Buyback Order",
        filters={"buyback_assessment": ["is", "set"], "docstatus": ["!=", 2]},
        fields=["name", "buyback_assessment", "kyc_verified"],
        limit_page_length=0,
    ):
        assessment = frappe.db.get_value(
            "Buyback Assessment", row.buyback_assessment, fields, as_dict=True
        )
        if not assessment:
            continue

        updates = {}
        for fieldname in fields:
            target = {
                "kyc_id_type": "customer_id_type",
                "kyc_id_number": "customer_id_number",
            }.get(fieldname, fieldname)
            if frappe.db.get_value("Buyback Order", row.name, target):
                continue
            value = assessment.get(fieldname)
            if not value and fieldname in (
                "customer_id_front", "customer_id_back", "customer_photo"
            ):
                value = _latest_private_attachment(row.buyback_assessment, fieldname)
            if value:
                updates[target] = value

        if updates:
            frappe.db.set_value("Buyback Order", row.name, updates, update_modified=False)

        if cint(row.kyc_verified):
            continue
        order = frappe.get_doc("Buyback Order", row.name)
        if all(
            (
                order.customer_id_type,
                order.customer_id_number,
                order.customer_id_front,
                order.customer_photo,
            )
        ):
            try:
                order.verify_kyc()
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"KYC backfill verification failed for {order.name}",
                )

