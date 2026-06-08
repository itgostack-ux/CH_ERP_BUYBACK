"""
Customer-facing buyback approval page.

This is a www page — accessible without login at:
  /buyback-approval?token=<hash>

Uses Frappe's website_route_rules to map the URL.
The page calls ``buyback.api.get_buyback_approval_details`` (allow_guest=True)
to get order details, then lets the customer trigger OTP verification.
"""
import frappe
from urllib.parse import parse_qs

no_cache = 1


def get_context(context):
    """Populate Jinja context for the approval page."""
    # Query params can be absent in form_dict on some website request paths,
    # so read from both form_dict and request.args for reliability.
    token = frappe.form_dict.get("token")
    if not token and getattr(frappe, "request", None):
        token = frappe.request.args.get("token")

    if not token and getattr(frappe, "local", None) and getattr(frappe.local, "request", None):
        query_string = frappe.local.request.environ.get("QUERY_STRING", "")
        token = (parse_qs(query_string).get("token") or [None])[0]

    # request.args may return bytes in some environments.
    if isinstance(token, bytes):
        token = token.decode("utf-8", errors="ignore")

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
            frappe.db.get_value("Warehouse", order.store, "warehouse_name")
            if order.store else ""
        ),
        "status": order.status,
        "device_photo_front": order.device_photo_front,
        "device_photo_back": order.device_photo_back,
        "otp_verified": order.otp_verified,
        "warranty_status": order.warranty_status,
        "mobile_no": order.mobile_no,
        "customer_payout_mode": order.customer_payout_mode,
        "customer_cash_receiver_name": order.customer_cash_receiver_name,
        "customer_upi_id": order.customer_upi_id,
        "customer_bank_account_holder": order.customer_bank_account_holder,
        "customer_bank_account_number": order.customer_bank_account_number,
        "customer_bank_ifsc": order.customer_bank_ifsc,
        "customer_bank_name": order.customer_bank_name,
        "customer_payout_notes": order.customer_payout_notes,
        "customer_photo": order.customer_photo,
        "customer_id_type": order.customer_id_type,
        "customer_id_number": order.customer_id_number,
        "customer_id_front": order.customer_id_front,
        "customer_id_back": order.customer_id_back,
        "kyc_verified": order.kyc_verified,
        "kyc_verified_at": order.kyc_verified_at,
    }
    context.no_cache = 1
