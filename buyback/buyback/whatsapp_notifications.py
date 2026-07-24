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
    company = frappe.db.get_value("Buyback Order", order_name, "company")
    settings = _get_settings(company)
    if not settings:
        return

    from ch_item_master.ch_core.whatsapp import send_template_message

    send_template_message(
        phone=mobile_no,
        event="buyback_otp",
        body_values={"1": otp_code},
        customer_name="Customer",
        ref_doctype="Buyback Order",
        ref_name=order_name,
        enqueue=False,  # OTP must be sent immediately
        company=company,
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


def send_otp(mobile_no: str, otp_code: str, purpose: str,
             ref_doctype: str = "", ref_name: str = "", email: str | None = None,
             company: str | None = None) -> dict:
    """Deliver an OTP across ALL available channels — SMS, WhatsApp and email.

    Each channel is best-effort and independent: a failure in one never blocks
    the others, so the customer receives the OTP wherever they can. WhatsApp
    routes via the company's account (derived from the reference doc when
    ``company`` is omitted). Returns a per-channel result dict.
    """
    results = {"sms": False, "whatsapp": False, "email": False}
    if not company and ref_doctype and ref_name:
        try:
            if frappe.get_meta(ref_doctype).has_field("company"):
                company = frappe.db.get_value(ref_doctype, ref_name, "company")
        except Exception:
            company = None

    # 1) SMS via the company's gateway (CH SMS Account) → global SMS Settings.
    try:
        from ch_item_master.ch_core.sms import send_company_sms, get_otp_expiry
        mins = get_otp_expiry(company)
        msg = (f"Your OTP for {purpose} is {otp_code}. "
               f"Valid for {mins} minutes. Do not share it with anyone.")
        send_company_sms([mobile_no], msg, company=company)
        results["sms"] = True
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"OTP SMS delivery failed for {mobile_no}")

    # 2) WhatsApp via the company's OTP template (from the library, event-mapped).
    try:
        from ch_item_master.ch_core.whatsapp import get_template, send_template_message
        tmpl, _ = get_template(company, "buyback_otp")
        if _get_settings(company) and tmpl:
            send_template_message(
                phone=mobile_no,
                event="buyback_otp",
                body_values={"1": otp_code},
                customer_name="Customer",
                ref_doctype=ref_doctype or "CH OTP Log",
                ref_name=ref_name or "",
                enqueue=False,  # OTP must go out immediately
                company=company,
            )
            results["whatsapp"] = True
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"OTP WhatsApp delivery failed for {mobile_no}")

    # 3) Email (resolve from mobile if not supplied).
    try:
        to_email = email or _get_email_for_mobile(mobile_no)
        if to_email:
            results["email"] = send_otp_email(to_email, otp_code, purpose, ref_name)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"OTP email delivery failed for {mobile_no}")

    return results


# ── Private helpers ──────────────────────────────────────────────────

def _get_settings(company=None):
    """Per-company WhatsApp account (credentials + templates) → global single."""
    from ch_item_master.ch_core.whatsapp import get_whatsapp_settings
    s = get_whatsapp_settings(company)
    return s if (s and s.enabled) else None


def _notify_awaiting_customer_approval(doc, phone, customer_name):
    """Send approval link via WhatsApp + Email when order moves to Awaiting Customer Approval."""
    from frappe.utils import fmt_money, get_url, escape_html

    approval_url = f"{get_url()}/buyback-approval?token={doc.approval_token}" if doc.approval_token else ""
    item_label = doc.item_name or doc.item or "your device"
    price_str = fmt_money(doc.final_price, currency="INR") if doc.final_price else ""

    # ── WhatsApp ─────────────────────────────────────────────────
    if approval_url and _get_settings(doc.company):
        try:
            from ch_item_master.ch_core.whatsapp import send_template_message

            send_template_message(
                phone=phone,
                event="buyback_customer_approval",
                body_values={
                    "1": customer_name,
                    "2": item_label,
                    "3": price_str,
                    "4": approval_url,
                },
                customer_name=customer_name,
                ref_doctype="Buyback Order",
                ref_name=doc.name,
                company=doc.company,
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
                company_label = (
                    frappe.get_cached_value("Company", doc.company, "company_name")
                    or doc.company
                    or frappe._("Our Store")
                )
                company_subject = str(company_label).replace("\r", " ").replace("\n", " ")
                company_html = escape_html(company_label)
                subject = f"{company_subject} | Buyback Approval | {doc.name}"
                html = f"""
                <div style="font-family:Segoe UI,Arial,sans-serif;max-width:620px;margin:auto;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden">
                    <div style="background:#0f172a;color:#ffffff;padding:12px 16px;font-weight:600">{company_html} - Buyback</div>
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
    settings = _get_settings(doc.company)
    if not settings:
        return

    from ch_item_master.ch_core.whatsapp import send_template_message

    send_template_message(
        phone=phone,
        event="buyback_order_created",
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
    settings = _get_settings(doc.company)
    if not settings:
        return

    from ch_item_master.ch_core.whatsapp import send_template_message

    from frappe.utils import fmt_money

    price_str = fmt_money(doc.final_price, currency="INR") if doc.final_price else ""

    send_template_message(
        phone=phone,
        event="buyback_approved",
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
    settings = _get_settings(doc.company)
    if not settings:
        return

    from ch_item_master.ch_core.whatsapp import send_template_message

    from frappe.utils import fmt_money

    price_str = fmt_money(doc.final_price, currency="INR") if doc.final_price else ""

    send_template_message(
        phone=phone,
        event="buyback_paid",
        body_values={
            "1": customer_name,
            "2": doc.name,
            "3": price_str,
        },
        customer_name=customer_name,
        ref_doctype="Buyback Order",
        ref_name=doc.name,
    )
