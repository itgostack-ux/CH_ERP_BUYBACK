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

    try:
        from buyback.api import get_buyback_approval_details

        details = get_buyback_approval_details(token)
    except Exception:
        context.error = "Invalid or expired approval link."
        return
    context.order = frappe._dict(details)
    context.no_cache = 1
