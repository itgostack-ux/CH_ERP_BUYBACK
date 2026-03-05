"""
Customer-facing buyback approval page.

This is a www page — accessible without login at:
  /buyback-approval?token=<hash>

Uses Frappe's website_route_rules to map the URL.
The page calls ``buyback.api.get_buyback_approval_details`` (allow_guest=True)
to get order details, then lets the customer trigger OTP verification.
"""
import frappe

no_cache = 1


def get_context(context):
    """Populate Jinja context for the approval page."""
    token = frappe.form_dict.get("token")
    context.token = token
    context.order = None
    context.error = None

    if not token:
        context.error = "No approval token provided."
        return

    order_name = frappe.db.get_value(
        "Buyback Order", {"approval_token": token, "docstatus": ["!=", 2]}, "name"
    )
    if not order_name:
        context.error = "Invalid or expired approval link."
        return

    order = frappe.get_doc("Buyback Order", order_name)
    context.order = {
        "name": order.name,
        "order_id": order.order_id,
        "customer_name": order.customer_name,
        "item_name": frappe.db.get_value("Item", order.item, "item_name") or order.item,
        "brand": order.brand,
        "imei_serial": order.imei_serial,
        "condition_grade": (
            frappe.db.get_value("Grade Master", order.condition_grade, "grade_name")
            if order.condition_grade else ""
        ),
        "final_price": order.final_price,
        "store_name": (
            frappe.db.get_value("CH Store", order.store, "store_name")
            if order.store else ""
        ),
        "status": order.status,
        "device_photo_front": order.device_photo_front,
        "device_photo_back": order.device_photo_back,
        "otp_verified": order.otp_verified,
        "warranty_status": order.warranty_status,
        "mobile_no": order.mobile_no,
    }
    context.no_cache = 1
