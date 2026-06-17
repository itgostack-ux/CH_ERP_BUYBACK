"""
Buyback Customer Approval E2E (targeted)

Covers:
1) Customer payout preference save via approval token should succeed.
2) Buyback audit log entry should be created without select-validation errors.
3) POS buyback detail API should auto-fetch customer payout fields.

Run:
  cd /home/palla/erpnext-bench
  ./env/bin/python3 apps/buyback/test_buyback_customer_approval_e2e.py
"""

import frappe
from frappe.utils import flt


SITE = "erpnext.local"
SITES_PATH = "/home/palla/erpnext-bench/sites"


def _init():
    frappe.init(site=SITE, sites_path=SITES_PATH)
    frappe.connect()
    frappe.set_user("Administrator")


def _pick_buyback_assessment():
    row = frappe.db.sql(
        """
        SELECT name
        FROM `tabBuyback Assessment`
        WHERE docstatus < 2
        ORDER BY modified DESC
        LIMIT 1
        """,
        as_dict=True,
    )
    if not row:
        raise AssertionError("No Buyback Assessment found for e2e test")
    return row[0].name


def _ensure_order(assessment_name):
    from ch_pos.api.pos_api import pos_start_buyback_order

    existing = frappe.db.get_value(
        "Buyback Order",
        {"buyback_assessment": assessment_name, "docstatus": ["!=", 2]},
        "name",
    )
    if existing:
        return existing

    profile = frappe.db.get_value("POS Profile", {}, "name")
    if not profile:
        raise AssertionError("No POS Profile found")

    out = pos_start_buyback_order(assessment_name=assessment_name, pos_profile=profile)
    return out["order_name"]


def run():
    _init()

    assessment_name = _pick_buyback_assessment()
    order_name = _ensure_order(assessment_name)

    order = frappe.get_doc("Buyback Order", order_name)
    if not order.approval_token:
        order.approval_token = frappe.generate_hash(length=32)
        order.flags.ignore_permissions = True
        order.save(ignore_permissions=True)

    if order.status not in ("Approved", "Awaiting Customer Approval", "Awaiting OTP", "OTP Verified"):
        order.db_set("status", "Approved", update_modified=True)
        order.reload()

    from buyback.api import save_customer_payout_preference

    res = save_customer_payout_preference(
        token=order.approval_token,
        payout_mode="UPI",
        upi_id="qa.user@upi",
        payout_notes="e2e approval link save",
    )
    assert res["name"] == order_name, "save_customer_payout_preference returned wrong order"

    order.reload()
    assert order.customer_payout_mode == "UPI", "customer_payout_mode not saved"
    assert order.customer_upi_id == "qa.user@upi", "customer_upi_id not saved"

    audit = frappe.db.get_value(
        "Buyback Audit Log",
        {
            "reference_doctype": "Buyback Order",
            "reference_name": order_name,
        },
        ["name", "action"],
        as_dict=True,
        order_by="creation desc",
    )
    assert audit, "No Buyback Audit Log entry created"
    assert audit.action in ("Customer Payout Updated", "Settlement Done"), (
        f"Unexpected audit action fallback: {audit.action}"
    )

    from ch_pos.api.pos_api import get_pos_buyback_detail

    detail = get_pos_buyback_detail(assessment_name)
    o = detail.get("order") or {}
    assert o.get("name") == order_name, "get_pos_buyback_detail order mismatch"
    assert o.get("customer_payout_mode") == "UPI", "POS auto-fetch missing customer_payout_mode"
    assert o.get("customer_upi_id") == "qa.user@upi", "POS auto-fetch missing customer_upi_id"

    print("PASS: buyback customer approval link save + POS auto-fetch + audit log compatibility")
    print(
        {
            "assessment": assessment_name,
            "order": order_name,
            "final_price": flt(order.final_price),
            "audit_action": audit.action,
            "payout_mode": o.get("customer_payout_mode"),
            "upi_id": o.get("customer_upi_id"),
        }
    )


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            frappe.destroy()
        except Exception:
            pass