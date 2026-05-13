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
    elif new_status == "Awaiting Customer Approval":
        _notify_awaiting_customer_approval(doc, phone, customer_name)
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


def send_otp_email(to_email: str, otp_code: str, purpose: str, ref_name: str = "") -> bool:
    """Send OTP via email. Used alongside WhatsApp for all OTP types."""
    if not to_email:
        return False
    subject = f"Your OTP for {purpose}"
    body = f"""<p>Dear Customer,</p>
<p>Your One-Time Password (OTP) for <strong>{frappe.utils.escape_html(purpose)}</strong>
{(' — ' + frappe.utils.escape_html(ref_name)) if ref_name else ''} is:</p>
<h2 style="letter-spacing:6px;font-size:32px;font-family:monospace;
    background:#f0f4ff;display:inline-block;padding:10px 24px;
    border-radius:8px;border:2px solid #c7d2fe">{frappe.utils.escape_html(otp_code)}</h2>
<p>This OTP is valid for <strong>5 minutes</strong>. Do not share it with anyone.</p>
<p style="color:#6b7280;font-size:12px">If you did not request this OTP, please ignore this email.</p>
"""
    try:
        frappe.sendmail(
            recipients=[to_email],
            subject=subject,
            message=body,
            delayed=False,
        )
        return True
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"OTP email delivery failed for {to_email}")
        return False


def _get_email_for_mobile(mobile_no: str) -> str:
    """Look up email by mobile number — checks User, Employee, and Customer tables."""
    if not mobile_no:
        return ""
    # 1. User table (covers managers/staff)
    email = frappe.db.get_value("User", {"mobile_no": mobile_no, "enabled": 1}, "email")
    if email:
        return email
    # 2. Employee table
    email = frappe.db.get_value(
        "Employee",
        {"cell_number": mobile_no, "status": "Active"},
        "prefered_email",
    )
    if email:
        return email
    # 3. Customer table
    email = frappe.db.get_value("Customer", {"mobile_no": mobile_no}, "email_id")
    return email or ""


# ── Private helpers ──────────────────────────────────────────────────

def _get_settings():
    try:
        s = frappe.get_cached_doc("CH WhatsApp Settings")
        return s if s.enabled else None
    except frappe.DoesNotExistError:
        return None


def _notify_awaiting_customer_approval(doc, phone, customer_name):
    """Send approval link via WhatsApp + Email when order moves to Awaiting Customer Approval."""
    from frappe.utils import fmt_money, get_url, escape_html

    approval_url = f"{get_url()}/buyback-approval?token={doc.approval_token}" if doc.approval_token else ""
    item_label = doc.item_name or doc.item or "your device"
    price_str = fmt_money(doc.final_price, currency="INR") if doc.final_price else ""

    # ── WhatsApp ─────────────────────────────────────────────────
    settings = _get_settings()
    template_name = getattr(settings, "buyback_customer_approval", "") if settings else ""
    if template_name and approval_url:
        try:
            from ch_item_master.ch_core.whatsapp import send_template_message

            send_template_message(
                phone=phone,
                template_name=template_name,
                body_values={
                    "1": customer_name,
                    "2": item_label,
                    "3": price_str,
                    "4": approval_url,
                },
                customer_name=customer_name,
                ref_doctype="Buyback Order",
                ref_name=doc.name,
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Buyback approval WhatsApp failed for {doc.name}")

    # ── Email ────────────────────────────────────────────────────
    if approval_url:
        try:
            customer_email = None
            if doc.customer:
                customer_email = frappe.db.get_value("Customer", doc.customer, "email_id")
            if not customer_email and doc.mobile_no:
                customer_email = frappe.db.get_value("Customer", {"mobile_no": doc.mobile_no}, "email_id")

            if customer_email:
                subject = f"Congruence Holdings | GoGizmo Buyback Approval | {doc.name}"
                html = f"""
                <div style="font-family:Segoe UI,Arial,sans-serif;max-width:620px;margin:auto;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden">
                    <div style="background:#0f172a;color:#ffffff;padding:12px 16px;font-weight:600">Congruence Holdings - GoGizmo Buyback</div>
                    <div style="padding:16px">
                    <h2 style="color:#1a1a2e;margin-top:0">Buyback Offer for Your Approval</h2>
                    <p>Hi {escape_html(customer_name)},</p>
                    <p>We have evaluated your <strong>{escape_html(item_label)}</strong>
                       and are offering <strong>{price_str}</strong>.</p>
                    <p>Please review and approve the offer by clicking the button below:</p>
                    <p style="text-align:center;margin:24px 0">
                        <a href="{escape_html(approval_url)}"
                           style="background:#28a745;color:#fff;padding:12px 32px;
                           text-decoration:none;border-radius:6px;font-size:16px;
                           display:inline-block">
                            Review &amp; Approve
                        </a>
                    </p>
                    <p style="color:#6b7280;font-size:13px">
                        Or copy this link: {escape_html(approval_url)}
                    </p>
                    <p style="color:#6b7280;font-size:12px">
                        Order: {doc.name} | This link is unique to your transaction.
                    </p>
                    </div>
                </div>
                """
                frappe.sendmail(
                    recipients=[customer_email],
                    subject=subject,
                    message=html,
                )
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Buyback approval email failed for {doc.name}")


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
