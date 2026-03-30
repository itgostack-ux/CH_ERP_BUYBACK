"""Buyback → WhatsApp notifications on Buyback Order status changes + OTP delivery."""

import frappe


def on_buyback_order_whatsapp(doc, method):
    """Hook: Buyback Order.on_update — send WhatsApp on key status transitions."""
    if frappe.flags.in_import or frappe.flags.in_migrate:
        return

    old = doc.get_doc_before_save()
    if not old:
        return

    old_status = old.status
    new_status = doc.status
    if old_status == new_status:
        return

    phone = doc.mobile_no
    if not phone:
        return

    customer_name = doc.customer_name or "Customer"

    if new_status == "Awaiting Approval" and old_status == "Draft":
        _notify_order_created(doc, phone, customer_name)
    elif new_status == "Approved":
        _notify_approved(doc, phone, customer_name)
    elif new_status == "Paid":
        _notify_paid(doc, phone, customer_name)


def send_otp_whatsapp(mobile_no: str, otp_code: str, order_name: str):
    """Send OTP via WhatsApp. Called from BuybackOrder.send_otp() after OTP generation."""
    settings = _get_settings()
    if not settings:
        return

    from ch_item_master.ch_core.whatsapp import send_template_message

    send_template_message(
        phone=mobile_no,
        template_name=settings.buyback_otp,
        body_values={"1": otp_code},
        customer_name="Customer",
        ref_doctype="Buyback Order",
        ref_name=order_name,
        enqueue=False,  # OTP must be sent immediately
    )


# ── Private helpers ──────────────────────────────────────────────────

def _get_settings():
    try:
        s = frappe.get_cached_doc("CH WhatsApp Settings")
        return s if s.enabled else None
    except frappe.DoesNotExistError:
        return None


def _notify_order_created(doc, phone, customer_name):
    settings = _get_settings()
    if not settings:
        return

    from ch_item_master.ch_core.whatsapp import send_template_message

    send_template_message(
        phone=phone,
        template_name=settings.buyback_order_created,
        body_values={
            "1": customer_name,
            "2": doc.name,
            "3": doc.item_name or "",
        },
        customer_name=customer_name,
        ref_doctype="Buyback Order",
        ref_name=doc.name,
    )


def _notify_approved(doc, phone, customer_name):
    settings = _get_settings()
    if not settings:
        return

    from ch_item_master.ch_core.whatsapp import send_template_message

    from frappe.utils import fmt_money

    price_str = fmt_money(doc.final_price, currency="INR") if doc.final_price else ""

    send_template_message(
        phone=phone,
        template_name=settings.buyback_approved,
        body_values={
            "1": customer_name,
            "2": doc.name,
            "3": doc.item_name or "",
            "4": price_str,
        },
        customer_name=customer_name,
        ref_doctype="Buyback Order",
        ref_name=doc.name,
    )


def _notify_paid(doc, phone, customer_name):
    settings = _get_settings()
    if not settings:
        return

    from ch_item_master.ch_core.whatsapp import send_template_message

    from frappe.utils import fmt_money

    price_str = fmt_money(doc.final_price, currency="INR") if doc.final_price else ""

    send_template_message(
        phone=phone,
        template_name=settings.buyback_paid,
        body_values={
            "1": customer_name,
            "2": doc.name,
            "3": price_str,
        },
        customer_name=customer_name,
        ref_doctype="Buyback Order",
        ref_name=doc.name,
    )
