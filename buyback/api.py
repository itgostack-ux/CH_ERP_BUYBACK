"""
Buyback API Endpoints
=====================
All mobile/API-facing endpoints for the buyback flow.
Every endpoint requires login (session or token auth).

Endpoint Reference:
  /api/method/buyback.api.<method>

Patterns followed (India Compliance / HRMS):
  - Type annotations on every parameter  (IC: require_type_annotated_api_methods)
  - Permission checks via doc.check_permission or frappe.has_permission
  - Custom exceptions from buyback.exceptions
  - All user-facing strings wrapped in _()
"""

import hashlib
import hmac
import json
import re

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import flt, now_datetime

from buyback.exceptions import (
    BuybackStatusError,
    BuybackValidationError,
)
from buyback.utils import (
    assert_buyback_scope,
    build_buyback_scope_sql,
    clear_fixed_window,
    get_buyback_data_scope,
    get_int_setting,
    increment_fixed_window,
    is_privileged_user,
    log_audit,
    require_configured_role,
    require_scoped_document_action,
    validate_bounded_text,
    validate_indian_phone,
)


# ---------------------------------------------------------------------------
# Token security helpers (used by every guest endpoint that authenticates by
# `approval_token`).  Centralised so TTL + single-use rules are consistent.
# ---------------------------------------------------------------------------

# Status set in which the approval link is still actionable. Anything outside
# this set rejects the token (closed/paid/rejected orders cannot be replayed).
_TOKEN_ACTIVE_STATUSES = {
    "Approved",
    "Awaiting Customer Approval",
    "Awaiting OTP",
    "OTP Verified",
}

# Statuses where bank/payout details may still be edited via the link.
# Once customer has approved, payout details are LOCKED to prevent
# token-replay attacks that re-route money (see C5 in the security audit).
_PAYOUT_EDITABLE_STATUSES = {
    "Approved",
    "Awaiting Customer Approval",
    "Awaiting OTP",
}


def _require_app_read(action: str, *doctypes: str) -> None:
    require_configured_role("app_access_roles", action=action)
    for doctype in doctypes:
        frappe.has_permission(doctype, ptype="read", throw=True)


def _mask_phone(phone: str | None) -> str:
    """Return a masked phone safe to echo back to a guest endpoint."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if len(digits) < 4:
        return "****"
    return f"{'*' * (len(digits) - 4)}{digits[-4:]}"


def _mask_identifier(value: str | None, visible: int = 4) -> str:
    """Mask a customer identifier while retaining a recognition suffix."""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= visible:
        return "*" * len(value)
    return f"{'*' * (len(value) - visible)}{value[-visible:]}"


def _mask_upi(value: str | None) -> str:
    value = (value or "").strip()
    if not value or "@" not in value:
        return _mask_identifier(value)
    local, handle = value.split("@", 1)
    return f"{local[:1]}***@{handle}"


def _mask_name(value: str | None) -> str:
    parts = [part for part in (value or "").split() if part]
    return " ".join(f"{part[:1]}***" for part in parts)


def _payout_audit_snapshot(values: dict) -> dict:
    return {
        "customer_payout_mode": values.get("customer_payout_mode"),
        "customer_cash_receiver_name": _mask_name(values.get("customer_cash_receiver_name")),
        "customer_upi_id": _mask_upi(values.get("customer_upi_id")),
        "customer_bank_account_holder": _mask_name(values.get("customer_bank_account_holder")),
        "customer_bank_account_number": _mask_identifier(
            values.get("customer_bank_account_number")
        ),
        "customer_bank_ifsc": _mask_identifier(values.get("customer_bank_ifsc"), visible=3),
        "customer_bank_name": values.get("customer_bank_name"),
        "has_payout_notes": bool(values.get("customer_payout_notes")),
    }


def _resolve_token(
    token: str,
    *,
    require_payout_editable: bool = False,
    for_update: bool = False,
) -> str:
    """Validate `approval_token` and return the order name.

    Raises if:
      - token is missing / unknown / order cancelled (docstatus == 2)
      - order has moved past the active customer-approval phase
      - configured token TTL since token issuance elapsed
      - require_payout_editable=True and customer has already approved
        (single-use lock against bank-account hijack)
    """
    from frappe.utils import time_diff_in_hours

    token = validate_bounded_text(token, _("Approval Token"), 256, required=True)
    token_digest = hashlib.sha256(token.encode()).hexdigest()
    fields = [
        "name",
        "status",
        "creation",
        "customer_approved",
        "customer_approved_at",
        "approval_token_digest",
    ]
    if frappe.get_meta("Buyback Order").has_field("approval_token_issued_at"):
        fields.append("approval_token_issued_at")
    row = frappe.db.get_value(
        "Buyback Order",
        {"approval_token_digest": token_digest, "docstatus": ["!=", 2]},
        fields,
        as_dict=True,
        for_update=for_update,
    )
    if not row:
        frappe.throw(_("Invalid or expired approval link."), exc=frappe.DoesNotExistError, title=_("API Error"))
    if not hmac.compare_digest(str(row.approval_token_digest or ""), token_digest):
        frappe.throw(_("Invalid or expired approval link."), exc=frappe.DoesNotExistError, title=_("API Error"))

    # Status gate — reject terminal / never-active orders
    if row.status not in _TOKEN_ACTIVE_STATUSES:
        frappe.throw(_("This approval link is no longer active."), exc=BuybackStatusError, title=_("API Error"))

    # TTL gate
    try:
        issued_at = row.get("approval_token_issued_at") or row.creation
        elapsed = time_diff_in_hours(now_datetime(), issued_at)
    except Exception:
        frappe.throw(
            _("This approval link has invalid issuance data. Please request a fresh link from the store."),
            exc=BuybackStatusError,
            title=_("Link Invalid"),
        )
    if elapsed < 0 or elapsed >= get_int_setting("approval_token_ttl_hours", 72):
        frappe.throw(
            _("This approval link has expired. Please request a fresh link from the store."),
            exc=BuybackStatusError,
            title=_("Link Expired"),
        )

    # Single-use payout lock — once the customer has approved, payout details
    # cannot be changed via the public link (bank-account-hijack defence).
    if require_payout_editable:
        if row.status not in _PAYOUT_EDITABLE_STATUSES or row.customer_approved:
            frappe.throw(
                _("Payout details are locked once the customer has approved. "
                  "Please visit the store to make changes."),
                exc=BuybackStatusError,
                title=_("Payout Locked"),
            )

    return row.name

# ── Step 1: Get Estimate ─────────────────────────────────────────


def _calculate_estimate(
    item_code: str,
    grade: str,
    warranty_status: str | None = None,
    device_age_months: int | str | None = None,
    responses: str | None = None,
    brand: str | None = None,
    item_group: str | None = None,
) -> dict:
    """
    Get an estimated buyback price for a device.

    Returns:
        dict with base_price, deductions, total_deductions, estimated_price
    """
    from buyback.buyback.pricing.engine import calculate_estimated_price

    resp_list = json.loads(responses) if isinstance(responses, str) else (responses or [])

    return calculate_estimated_price(
        item_code=item_code,
        grade=grade,
        warranty_status=warranty_status,
        device_age_months=device_age_months,
        responses=resp_list,
        brand=brand,
        item_group=item_group,
    )


@frappe.whitelist()
def get_estimate(
    item_code: str,
    grade: str,
    warranty_status: str | None = None,
    device_age_months: int | str | None = None,
    responses: str | None = None,
    brand: str | None = None,
    item_group: str | None = None,
) -> dict:
    _require_app_read(
        _("calculate a Buyback estimate"),
        "Item",
        "Grade Master",
        "Buyback Price Master",
        "Buyback Pricing Rule",
    )
    frappe.get_doc("Item", item_code).check_permission("read")
    return _calculate_estimate(
        item_code=item_code,
        grade=grade,
        warranty_status=warranty_status,
        device_age_months=device_age_months,
        responses=responses,
        brand=brand,
        item_group=item_group,
    )


# ── Step 2: Submit Assessment & Create Inspection ────────────────


@frappe.whitelist(methods=["POST"])
def submit_assessment(assessment_name: str) -> dict:
    """Submit a draft assessment."""
    doc = frappe.get_doc("Buyback Assessment", assessment_name)
    doc.check_permission("write")
    doc.submit_assessment()
    return {
        "name": doc.name,
        "status": doc.status,
        "estimated_price": doc.estimated_price,
        "quoted_price": doc.quoted_price,
    }


@frappe.whitelist(methods=["POST"])
def submit_assessment_imei_validation(assessment_name: str, status: str, screenshot: str | None = None,
                                       remarks: str | None = None) -> dict:
    """Record the manual Sanchar Saathi (CEIR) IMEI check at assessment/intake stage.

    Optional here, but recommended before inspection starts — see
    BuybackAssessment.create_inspection() which hard-gates on this.
    """
    doc = frappe.get_doc("Buyback Assessment", assessment_name)
    doc.check_permission("write")
    return doc.submit_imei_validation(status=status, screenshot=screenshot, remarks=remarks)


@frappe.whitelist(methods=["POST"])
def create_inspection_from_assessment(
    assessment_name: str,
    checklist_template: str | None = None,
) -> dict:
    """Create a Buyback Inspection directly from a submitted assessment.

    Returns:
        dict with inspection details (name, inspection_id, status)
    """
    doc = frappe.get_doc("Buyback Assessment", assessment_name)
    doc.check_permission("write")
    inspection = doc.create_inspection(checklist_template=checklist_template)

    return {
        "name": inspection.name,
        "inspection_id": inspection.inspection_id,
        "status": inspection.status,
        "assessment_name": doc.name,
    }


@frappe.whitelist()
def get_assessment(assessment_name: str) -> dict:
    """Get assessment details including responses and linked inspection."""
    require_configured_role("app_access_roles", action=_("view Buyback assessments"))
    doc = frappe.get_doc("Buyback Assessment", assessment_name)
    doc.check_permission("read")
    assert_buyback_scope(store=doc.store, company=doc.company)

    return {
        "name": doc.name,
        "assessment_id": doc.assessment_id,
        "source": doc.source,
        "status": doc.status,
        "customer": doc.customer,
        "mobile_no": doc.mobile_no,
        "item": doc.item,
        "item_name": frappe.db.get_value("Item", doc.item, "item_name") if doc.item else "",
        "brand": doc.brand,
        "imei_serial": doc.imei_serial,
        "estimated_grade": doc.estimated_grade,
        "estimated_price": doc.estimated_price,
        "quoted_price": doc.quoted_price,
        "buyback_inspection": doc.buyback_inspection,
        "expires_on": str(doc.expires_on) if doc.expires_on else None,
        "responses": [
            {
                "question": r.question,
                "question_code": r.question_code,
                "question_text": r.question_text,
                "answer_value": r.answer_value,
                "answer_label": r.answer_label,
                "price_impact_percent": r.price_impact_percent,
            }
            for r in (doc.responses or [])
        ],
    }


# ── Step 3: Create / Manage Inspection ───────────────────────────


@frappe.whitelist(methods=["POST"])
def create_inspection(
    assessment_name: str,
    checklist_template: str | None = None,
) -> dict:
    """Create a Buyback Inspection from a submitted assessment.

    This is an alternative API endpoint — same as create_inspection_from_assessment.
    """
    return create_inspection_from_assessment(assessment_name, checklist_template)


@frappe.whitelist(methods=["POST"])
def start_inspection(inspection_name: str) -> dict:
    """Start an inspection."""
    doc = frappe.get_doc("Buyback Inspection", inspection_name)
    doc.check_permission("write")
    doc.start_inspection()
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist(methods=["POST"])
def complete_inspection(
    inspection_name: str,
    condition_grade: str,
    revised_price: float | str | None = None,
    results: str | None = None,
    price_override_reason: str | None = None,
) -> dict:
    """Complete an inspection with results and grade."""
    doc = frappe.get_doc("Buyback Inspection", inspection_name)
    doc.check_permission("write")

    doc.post_inspection_grade = condition_grade
    if revised_price is not None:
        doc.revised_price = flt(revised_price)
    if price_override_reason:
        doc.price_override_reason = price_override_reason

    # Update results if provided
    if results:
        result_list = json.loads(results) if isinstance(results, str) else results
        for r in result_list:
            for row in doc.results:
                if row.check_code == r.get("check_code"):
                    row.result = r.get("result")
                    row.notes = r.get("notes", "")
                    break

    doc.complete_inspection()
    return {
        "name": doc.name,
        "inspection_id": doc.inspection_id,
        "status": doc.status,
        "condition_grade": doc.condition_grade,
        "revised_price": doc.revised_price,
    }


# ── Step 5: Create Order ─────────────────────────────────────────


def _carry_forward_imei_validation(buyback_assessment: str | None, order_doc) -> None:
    """Copy a 'Verified Clean' Sanchar Saathi check from Assessment to a new Order.

    Staff who already did the IMEI check at intake (Buyback Assessment)
    shouldn't have to repeat it when the Buyback Order is created from
    that assessment — only carry forward a clean result; anything else
    (Pending/Could Not Verify) still requires a fresh check at Order stage,
    and a bad result would already have cancelled the assessment, so a new
    order couldn't be created from it anyway.
    """
    if not buyback_assessment:
        return
    row = frappe.db.get_value(
        "Buyback Assessment", buyback_assessment,
        ["imei_validation_status", "imei_validation_screenshot",
         "imei_validation_checked_by", "imei_validation_checked_at", "imei_validation_remarks"],
        as_dict=True,
    )
    if row and row.imei_validation_status == "Verified Clean":
        order_doc.imei_validation_status = row.imei_validation_status
        order_doc.imei_validation_screenshot = row.imei_validation_screenshot
        order_doc.imei_validation_checked_by = row.imei_validation_checked_by
        order_doc.imei_validation_checked_at = row.imei_validation_checked_at
        order_doc.imei_validation_remarks = row.imei_validation_remarks


def _carry_forward_lock_clearance(buyback_inspection: str | None, order_doc) -> None:
    """Copy FRP/iCloud lock-clearance from a completed Inspection to a new Order.

    `complete_inspection()` already hard-requires `account_lock_cleared` to
    be set, so if an Inspection is linked, this is always available — carry
    it forward so staff aren't asked twice. Walk-in orders with no
    Inspection record leave this unset, and the Order-level gate
    (`_validate_lock_clearance_before_kyc`) requires it directly.
    """
    if not buyback_inspection:
        return
    row = frappe.db.get_value(
        "Buyback Inspection", buyback_inspection,
        ["account_lock_cleared", "account_lock_check_notes"],
        as_dict=True,
    )
    if row and row.account_lock_cleared:
        order_doc.account_lock_cleared = row.account_lock_cleared
        order_doc.account_lock_check_notes = row.account_lock_check_notes


@frappe.whitelist(methods=["POST"])
def create_order(
    customer: str,
    mobile_no: str,
    store: str,
    item: str,
    condition_grade: str,
    final_price: float | str,
    buyback_assessment: str | None = None,
    buyback_inspection: str | None = None,
    imei_serial: str | None = None,
    warranty_status: str | None = None,
    brand: str | None = None,
) -> dict:
    """Create a Buyback Order (submittable)."""
    require_configured_role("order_operation_roles", action=_("create Buyback orders"))
    frappe.has_permission("Buyback Order", ptype="create", throw=True)
    mobile_no = validate_indian_phone(mobile_no, "Mobile No")

    customer_doc = frappe.get_doc("Customer", customer)
    customer_doc.check_permission("read")
    item_doc = frappe.get_doc("Item", item)
    item_doc.check_permission("read")
    grade_doc = frappe.get_doc("Grade Master", condition_grade)
    grade_doc.check_permission("read")
    store_doc = frappe.get_doc("Warehouse", store)
    store_doc.check_permission("read")
    if store_doc.is_group and not store_doc.get("ch_is_buyback_enabled"):
        frappe.throw(_("The selected store is not enabled for Buyback."), frappe.PermissionError)
    company = store_doc.company
    assert_buyback_scope(warehouse=store, company=company)

    known_phones = {
        validate_indian_phone(phone, "Customer Mobile")
        for phone in (
            customer_doc.get("mobile_no"),
            customer_doc.get("ch_alternate_phone"),
            customer_doc.get("ch_whatsapp_number"),
        )
        if phone
    }
    if known_phones and mobile_no not in known_phones:
        frappe.throw(_("Mobile number does not match the selected Customer."), frappe.PermissionError)

    authoritative_price = 0
    if buyback_inspection:
        inspection = frappe.get_doc("Buyback Inspection", buyback_inspection)
        inspection.check_permission("read")
        assert_buyback_scope(store=inspection.store, company=inspection.company)
        if inspection.status != "Completed":
            frappe.throw(_("Buyback Inspection must be completed before order creation."))
        if inspection.customer != customer or inspection.item != item or inspection.store != store:
            frappe.throw(_("Inspection customer, item and store must match the order."), frappe.PermissionError)
        if buyback_assessment and inspection.buyback_assessment != buyback_assessment:
            frappe.throw(_("Inspection does not belong to the selected assessment."), frappe.PermissionError)
        buyback_assessment = inspection.buyback_assessment or buyback_assessment
        authoritative_price = flt(inspection.revised_price) or flt(inspection.quoted_price)
    elif buyback_assessment:
        assessment = frappe.get_doc("Buyback Assessment", buyback_assessment)
        assessment.check_permission("read")
        assert_buyback_scope(store=assessment.store, company=assessment.company)
        if assessment.status not in {"Submitted", "Inspection Created", "Inspected", "Quoted"}:
            frappe.throw(_("Assessment is not ready for order creation."))
        if assessment.customer != customer or assessment.item != item or assessment.store != store:
            frappe.throw(_("Assessment customer, item and store must match the order."), frappe.PermissionError)
        authoritative_price = flt(assessment.quoted_price) or flt(assessment.estimated_price)
    else:
        from buyback.buyback.pricing.engine import calculate_estimated_price

        pricing = calculate_estimated_price(
            item_code=item,
            grade=condition_grade,
            warranty_status=warranty_status,
            brand=brand or item_doc.brand,
            item_group=item_doc.item_group,
        )
        authoritative_price = flt(pricing.get("estimated_price"))

    if authoritative_price <= 0:
        frappe.throw(_("An authoritative Buyback price could not be resolved."))
    if abs(flt(final_price) - authoritative_price) > 0.01:
        frappe.throw(
            _("Final price must match the assessment, inspection, or configured pricing result."),
            frappe.PermissionError,
        )

    # Market-standard store gate (Cashify partner stores, Samsung Exchange
    # authorized centres, Best Buy Trade-In stores): the target Warehouse must
    # be flagged as buyback-enabled. Controlled by Buyback Settings so pilots
    # can bypass with an explicit setting flip.
    _require_store_gate = frappe.db.get_single_value(
        "Buyback Settings", "require_buyback_enabled_store"
    )
    # Default ON (missing/None on fresh installs → treat as enabled).
    if _require_store_gate is None or int(_require_store_gate or 0):
        _wh_enabled = frappe.db.get_value(
            "Warehouse", store, "ch_is_buyback_enabled"
        )
        if not _wh_enabled:
            frappe.throw(
                _(
                    "Warehouse {0} is not a buyback-enabled store. "
                    "Set 'Buyback Enabled' on the Warehouse or disable "
                    "'Require Buyback-Enabled Store' in Buyback Settings."
                ).format(frappe.bold(store)),
                title=_("Store Not Buyback-Enabled"),
            )

    doc = frappe.get_doc(
        {
            "doctype": "Buyback Order",
            "customer": customer,
            "mobile_no": mobile_no,
            "store": store,
            "company": company,
            "item": item,
            "condition_grade": condition_grade,
            "final_price": authoritative_price,
            "buyback_assessment": buyback_assessment,
            "buyback_inspection": buyback_inspection,
            "imei_serial": imei_serial,
            "warranty_status": warranty_status,
            "brand": brand,
        }
    )
    _carry_forward_imei_validation(buyback_assessment, doc)
    _carry_forward_lock_clearance(buyback_inspection, doc)
    doc.insert()
    doc.submit()

    return {
        "name": doc.name,
        "order_id": doc.order_id,
        "status": doc.status,
        "requires_approval": doc.requires_approval,
        "final_price": doc.final_price,
        "approval_token": doc.approval_token,
        "approval_url": f"/buyback-approval?token={doc.approval_token}",
    }


# ── Step 6: Approve / Reject Order ───────────────────────────────


@frappe.whitelist(methods=["POST"])
def approve_order(order_name: str, remarks: str | None = None) -> dict:
    """Manager approves a buyback order."""
    doc = frappe.get_doc("Buyback Order", order_name)
    doc.check_permission("write")
    doc.approve(remarks)
    return {"name": doc.name, "status": doc.status, "approved_by": doc.approved_by}


@frappe.whitelist(methods=["POST"])
def reject_order(order_name: str, remarks: str | None = None) -> dict:
    """Manager rejects a buyback order."""
    doc = frappe.get_doc("Buyback Order", order_name)
    doc.check_permission("write")
    doc.reject(remarks)
    return {"name": doc.name, "status": doc.status}


# ── Customer Approval + Settlement ───────────────────────────────


@frappe.whitelist(methods=["POST"])
def customer_approve_offer(
    order_name: str,
    method: str = "In-Store Signature",
) -> dict:
    """Customer approves the revised/final price on a buyback order.

    Required when inspection price differs from the original quoted price.
    """
    doc = frappe.get_doc("Buyback Order", order_name)
    require_scoped_document_action(
        doc, "order_operation_roles", _("record customer Buyback approval")
    )
    if method != "In-Store Signature":
        frappe.throw(_("Staff approval must use the in-store signature flow."), frappe.PermissionError)
    doc.customer_approve("In-Store Signature")
    return {
        "name": doc.name,
        "status": doc.status,
        "customer_approved": doc.customer_approved,
        "customer_approved_at": str(doc.customer_approved_at),
    }


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=60, methods=["POST"], ip_based=True)
def customer_approve_via_token(token: str, method: str = "SMS Link") -> dict:
    """Customer approves offer via the token-based approval link (no login).

    Used from the customer-facing approval page.
    """
    method = validate_bounded_text(method, _("Approval Method"), 30, required=True)
    order_name = _resolve_token(token, for_update=True)

    doc = frappe.get_doc("Buyback Order", order_name)
    if method != "SMS Link":
        frappe.throw(_("Approval-link confirmations must use SMS Link."), frappe.PermissionError)
    doc.customer_approve("SMS Link", token_authorized=True)
    return {
        "name": doc.name,
        "status": doc.status,
        "customer_approved": doc.customer_approved,
    }


def _validate_customer_payout_inputs(
    payout_mode: str,
    cash_receiver_name: str | None = None,
    upi_id: str | None = None,
    bank_account_holder: str | None = None,
    bank_account_number: str | None = None,
    bank_ifsc: str | None = None,
    bank_name: str | None = None,
    payout_notes: str | None = None,
) -> dict:
    """Validate and normalize customer payout preference input."""
    mode = validate_bounded_text(payout_mode, _("Payout Mode"), 30, required=True)
    allowed_modes = {"Cash", "UPI", "Bank Transfer"}
    if mode not in allowed_modes:
        frappe.throw(
            _("Invalid payout mode. Allowed values: Cash, UPI, Bank Transfer."),
            exc=BuybackValidationError,
        )

    data = {
        "customer_payout_mode": mode,
        "customer_cash_receiver_name": validate_bounded_text(
            cash_receiver_name, _("Receiver Name"), 140
        ),
        "customer_upi_id": validate_bounded_text(upi_id, _("UPI ID"), 140),
        "customer_bank_account_holder": validate_bounded_text(
            bank_account_holder, _("Account Holder Name"), 140
        ),
        "customer_bank_account_number": validate_bounded_text(
            bank_account_number, _("Account Number"), 34
        ),
        "customer_bank_ifsc": validate_bounded_text(
            bank_ifsc, _("IFSC Code"), 11
        ).upper(),
        "customer_bank_name": validate_bounded_text(bank_name, _("Bank Name"), 140),
        "customer_payout_notes": validate_bounded_text(
            payout_notes, _("Payout Notes"), 500
        ),
    }

    if mode == "Cash" and not data["customer_cash_receiver_name"]:
        frappe.throw(
            _("Receiver name is required for Cash payout."),
            exc=BuybackValidationError,
        )
    if mode == "Cash":
        data.update(
            {
                "customer_upi_id": "",
                "customer_bank_account_holder": "",
                "customer_bank_account_number": "",
                "customer_bank_ifsc": "",
                "customer_bank_name": "",
            }
        )

    if mode == "UPI" and not data["customer_upi_id"]:
        frappe.throw(
            _("UPI ID is required for UPI payout."),
            exc=BuybackValidationError,
        )
    if data["customer_upi_id"] and not re.fullmatch(
        r"[A-Za-z0-9._-]{2,100}@[A-Za-z][A-Za-z0-9._-]{1,38}",
        data["customer_upi_id"],
    ):
        frappe.throw(_("Enter a valid UPI ID."), exc=BuybackValidationError)
    if mode == "UPI":
        data.update(
            {
                "customer_cash_receiver_name": "",
                "customer_bank_account_holder": "",
                "customer_bank_account_number": "",
                "customer_bank_ifsc": "",
                "customer_bank_name": "",
            }
        )

    if mode == "Bank Transfer":
        data["customer_cash_receiver_name"] = ""
        data["customer_upi_id"] = ""
        missing = []
        if not data["customer_bank_account_holder"]:
            missing.append(_("Account Holder Name"))
        if not data["customer_bank_account_number"]:
            missing.append(_("Account Number"))
        if not data["customer_bank_ifsc"]:
            missing.append(_("IFSC Code"))
        if missing:
            frappe.throw(
                _("Missing required bank details: {0}").format(", ".join(missing)),
                exc=BuybackValidationError,
            )
        if not re.fullmatch(r"[0-9]{6,34}", data["customer_bank_account_number"]):
            frappe.throw(
                _("Bank account number must contain 6 to 34 digits."),
                exc=BuybackValidationError,
            )
        if not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", data["customer_bank_ifsc"]):
            frappe.throw(_("Enter a valid IFSC code."), exc=BuybackValidationError)

    return data


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=60, methods=["POST"], ip_based=True)
def save_customer_payout_preference(
    token: str,
    payout_mode: str,
    cash_receiver_name: str | None = None,
    upi_id: str | None = None,
    bank_account_holder: str | None = None,
    bank_account_number: str | None = None,
    bank_ifsc: str | None = None,
    bank_name: str | None = None,
    payout_notes: str | None = None,
) -> dict:
    """Save customer-selected payout mode/details from approval link.

    This captures customer payout preference for the accounts team before
    payment processing.
    """
    order_name = _resolve_token(
        token,
        require_payout_editable=True,
        for_update=True,
    )

    doc = frappe.get_doc("Buyback Order", order_name)
    allowed_status = {"Approved", "Awaiting Customer Approval", "Awaiting OTP", "OTP Verified"}
    if doc.status not in allowed_status:
        frappe.throw(
            _("Payout details can be updated only when order is in approval stage."),
            exc=BuybackStatusError,
        )

    data = _validate_customer_payout_inputs(
        payout_mode=payout_mode,
        cash_receiver_name=cash_receiver_name,
        upi_id=upi_id,
        bank_account_holder=bank_account_holder,
        bank_account_number=bank_account_number,
        bank_ifsc=bank_ifsc,
        bank_name=bank_name,
        payout_notes=payout_notes,
    )

    old_values = {
        "customer_payout_mode": doc.customer_payout_mode,
        "customer_cash_receiver_name": doc.customer_cash_receiver_name,
        "customer_upi_id": doc.customer_upi_id,
        "customer_bank_account_holder": doc.customer_bank_account_holder,
        "customer_bank_account_number": doc.customer_bank_account_number,
        "customer_bank_ifsc": doc.customer_bank_ifsc,
        "customer_bank_name": doc.customer_bank_name,
        "customer_payout_notes": doc.customer_payout_notes,
    }

    updated_at = now_datetime()
    doc.db_set(
        {
            **data,
            "customer_payout_updated_at": updated_at,
            "customer_payout_updated_by": "Customer (via approval link)",
        },
        update_modified=True,
    )
    doc.reload()

    new_values = {
        "customer_payout_mode": doc.customer_payout_mode,
        "customer_cash_receiver_name": doc.customer_cash_receiver_name,
        "customer_upi_id": doc.customer_upi_id,
        "customer_bank_account_holder": doc.customer_bank_account_holder,
        "customer_bank_account_number": doc.customer_bank_account_number,
        "customer_bank_ifsc": doc.customer_bank_ifsc,
        "customer_bank_name": doc.customer_bank_name,
        "customer_payout_notes": doc.customer_payout_notes,
    }
    if old_values != new_values:
        log_audit(
            "Customer Payout Updated",
            "Buyback Order",
            doc.name,
            old_value=_payout_audit_snapshot(old_values),
            new_value=_payout_audit_snapshot(new_values),
            reason="Updated via approval link",
        )

    return {
        "name": doc.name,
        "status": doc.status,
        "customer_payout_mode": doc.customer_payout_mode,
        "customer_payout_updated_at": str(doc.customer_payout_updated_at),
    }


@frappe.whitelist(methods=["POST"])
def select_settlement_type(
    order_name: str,
    settlement_type: str,
    new_item: str | None = None,
    new_device_price: float | str | None = None,
) -> dict:
    """Select buyback or exchange settlement for an order.

    Args:
        order_name: Buyback Order name
        settlement_type: "Buyback" or "Exchange"
        new_item: Required if Exchange — item code for new device
        new_device_price: Optional — price of new device (auto-fetched if omitted)
    """
    doc = frappe.get_doc("Buyback Order", order_name)
    require_scoped_document_action(
        doc, "order_operation_roles", _("select a Buyback settlement type")
    )
    doc.select_settlement_type(
        settlement_type,
        new_item=new_item,
        new_device_price=flt(new_device_price) if new_device_price else None,
    )
    return {
        "name": doc.name,
        "settlement_type": doc.settlement_type,
        "exchange_discount": doc.exchange_discount,
        "balance_to_pay": doc.balance_to_pay,
        "new_device_price": doc.new_device_price,
    }


# ── Step 7: OTP Verification ─────────────────────────────────────


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=3, seconds=300, methods=["POST"], ip_based=True)
def send_otp(order_name: str = None, token: str = None) -> dict:
    """Send OTP for buyback order confirmation.

    Accepts either order_name (for logged-in users) or token (for guest approval page).
    Tightened from 5/300s to 3/300s to throttle spam to the customer's phone.
    Per-order cap below prevents multi-IP bypass.
    """
    if token:
        order_name = _resolve_token(token)
    elif not order_name:
        frappe.throw(_("order_name or token is required."))
    elif frappe.session.user == "Guest":
        frappe.throw(_("An approval token is required."), frappe.PermissionError)
    else:
        order_name = validate_bounded_text(
            order_name, _("Buyback Order"), 140, required=True
        )

    doc = frappe.get_doc("Buyback Order", order_name)
    if not token:
        doc.check_permission("write")

    # Reserve the per-order send before dispatch. Redis INCRBY prevents
    # parallel workers from all observing the same stale count.
    sent = increment_fixed_window("otp-send", order_name, 3600)
    send_limit = get_int_setting("otp_send_limit_per_hour", 5)
    if sent > send_limit:
        frappe.throw(
            _("OTP send limit reached for this order. Please contact the store."),
            frappe.PermissionError,
            title=_("Rate Limit Exceeded"),
        )
    doc.send_otp(token_authorized=bool(token))

    # NEVER echo the full mobile number — leaks PII to anyone with a token.
    return {"status": "sent", "message": _("OTP sent to {0}").format(_mask_phone(doc.mobile_no))}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=300, methods=["POST"], ip_based=True)
def verify_otp(order_name: str = None, otp_code: str = "", token: str = None) -> dict:
    """Verify customer OTP for a buyback order.

    Accepts either order_name (for logged-in users) or token (for guest approval page).
    Attempt limits and the counter window are read from Buyback Settings.
    """
    otp_code = validate_bounded_text(otp_code, _("OTP"), 10, required=True)
    if token:
        order_name = _resolve_token(token)
    elif not order_name:
        frappe.throw(_("order_name or token is required."))
    elif frappe.session.user == "Guest":
        frappe.throw(_("An approval token is required."), frappe.PermissionError)
    else:
        order_name = validate_bounded_text(
            order_name, _("Buyback Order"), 140, required=True
        )

    source_ip = frappe.local.request.remote_addr if frappe.local.request else "unknown"

    ip_attempt_limit = get_int_setting("otp_attempt_limit_per_ip", 5)
    order_attempt_limit = get_int_setting("otp_attempt_limit_per_order", 20)
    attempt_window_minutes = get_int_setting("otp_attempt_window_minutes", 15)
    attempt_window_seconds = attempt_window_minutes * 60

    doc = frappe.get_doc("Buyback Order", order_name)
    if not token:
        doc.check_permission("write")

    # Reserve both attempts before verifying. This closes the check/update
    # race while preserving the per-order+IP and global per-order scopes.
    ip_identity = f"{order_name}:{source_ip}"
    ip_attempts = increment_fixed_window(
        "otp-verify-ip", ip_identity, attempt_window_seconds
    )
    if ip_attempts > ip_attempt_limit:
        frappe.throw(
            _("Too many OTP attempts from this IP. Please wait {0} minutes.").format(
                attempt_window_minutes
            ),
            frappe.PermissionError,
            title=_("Rate Limit Exceeded"),
        )

    order_attempts = increment_fixed_window(
        "otp-verify-order", order_name, attempt_window_seconds
    )
    if order_attempts > order_attempt_limit:
        frappe.throw(
            _("Too many OTP attempts for this order. "
              "Please wait {0} minutes before trying again.").format(
                attempt_window_minutes
            ),
            frappe.PermissionError,
            title=_("Rate Limit Exceeded"),
        )

    result = doc.verify_otp(otp_code)

    if result.get("valid"):
        # Clear counters on successful verification
        clear_fixed_window("otp-verify-ip", ip_identity)
        clear_fixed_window("otp-verify-order", order_name)

    return result


@frappe.whitelist(methods=["POST"])
def resend_customer_approval_link(order_name: str, reason: str | None = None) -> dict:
    """Regenerate and resend approval link, keeping an audit history."""
    from buyback.buyback.whatsapp_notifications import _notify_awaiting_customer_approval

    doc = frappe.get_doc("Buyback Order", order_name)
    require_scoped_document_action(
        doc, "order_operation_roles", _("resend a customer approval link")
    )

    if doc.status not in {"Approved", "Awaiting Customer Approval", "Awaiting OTP", "OTP Verified"}:
        frappe.throw(
            _("Approval link can be resent only during customer-approval stages."),
            exc=BuybackStatusError,
        )

    old_token = doc.approval_token
    doc.flags.ch_token_rotation_authorized = True
    doc.approval_token = frappe.generate_hash(length=32)
    doc.approval_token_digest = hashlib.sha256(doc.approval_token.encode()).hexdigest()
    if doc.meta.has_field("approval_token_issued_at"):
        doc.approval_token_issued_at = now_datetime()
    doc.save()

    phone = doc.mobile_no
    if phone:
        _notify_awaiting_customer_approval(doc, phone, doc.customer_name or "Customer")

    approval_url = f"{frappe.utils.get_url()}/buyback-approval?token={doc.approval_token}"
    log_audit(
        "Customer Approval Link Resent",
        "Buyback Order",
        doc.name,
        old_value={"had_active_token": bool(old_token)},
        new_value={"token_rotated": True, "issued_at": str(now_datetime())},
        reason=(reason or "Manual resend from desk"),
    )

    return {
        "name": doc.name,
        "status": doc.status,
        "approval_url": approval_url,
        "resent_to_mobile": bool(phone),
    }


@frappe.whitelist(methods=["POST"])
def request_price_exception(order_name: str, requested_price: float | str, reason: str) -> dict:
    """Raise a manager-governed exception for negotiated buyback price changes."""
    if not reason or not str(reason).strip():
        frappe.throw(_("Reason is required to request a price exception."), exc=BuybackValidationError)

    doc = frappe.get_doc("Buyback Order", order_name)
    require_scoped_document_action(
        doc, "order_operation_roles", _("request a Buyback price exception")
    )

    current_price = flt(doc.final_price)
    requested = flt(requested_price)
    if requested <= 0:
        frappe.throw(_("Requested price must be greater than zero."), exc=BuybackValidationError)
    if requested == current_price:
        frappe.throw(_("Requested price is same as current final price."), exc=BuybackValidationError)

    if not frappe.db.exists("CH Exception Type", "Exchange Value Override"):
        frappe.throw(_("Exception Type 'Exchange Value Override' is not configured."), exc=BuybackValidationError)

    from ch_item_master.ch_item_master.exception_api import raise_exception

    ex = raise_exception(
        exception_type="Exchange Value Override",
        company=doc.company,
        reason=(
            f"Negotiated price change requested on {doc.name}: "
            f"₹{current_price:,.2f} -> ₹{requested:,.2f}. "
            f"Reason: {str(reason).strip()}"
        ),
        requested_value=abs(requested - current_price),
        original_value=current_price,
        reference_doctype="Buyback Order",
        reference_name=doc.name,
        item_code=doc.item,
        serial_no=doc.imei_serial,
        store_warehouse=doc.store,
        customer=doc.customer,
    )

    log_audit(
        "Price Exception Requested",
        "Buyback Order",
        doc.name,
        old_value={"current_final_price": current_price},
        new_value={
            "requested_final_price": requested,
            "exception_request": ex,
            "requested_by": frappe.session.user,
        },
        reason=str(reason).strip(),
    )

    return {
        "name": doc.name,
        "current_price": current_price,
        "requested_price": requested,
        "exception_request": ex,
    }


# ── Step 8: Payment ──────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def record_payment(
    order_name: str,
    payment_method: str,
    amount: float | str,
    transaction_reference: str | None = None,
) -> dict:
    """Record a payment against a buyback order.

    Hardened for go-live:
      • amount must be > 0 (rejects zero/negative early with a clear error)
      • payment_method is mandatory
      • transaction_reference is mandatory for non-Cash modes (idempotency
        anchor + bank reconciliation hook)
      • parent doc is locked with SELECT … FOR UPDATE before append + save
        so concurrent record_payment calls cannot double-credit (P0-5)
      • duplicate (method, reference) pairs are rejected as idempotency
        keys — replaying the same gateway callback / button click cannot
        create a second payout row
    """
    amt = flt(amount)
    if amt <= 0:
        frappe.throw(
            _("Payment amount must be greater than zero."),
            exc=BuybackValidationError,
            title=_("Invalid Payment"),
        )
    method = (payment_method or "").strip()
    if not method:
        frappe.throw(
            _("Payment method is required."),
            exc=BuybackValidationError,
            title=_("Invalid Payment"),
        )
    ref = (transaction_reference or "").strip()
    mode_type = (frappe.db.get_value("Mode of Payment", method, "type") or "").strip()
    if mode_type != "Cash" and not ref:
        frappe.throw(
            _("Transaction reference is required for non-cash payment mode {0}.").format(
                frappe.bold(method)
            ),
            exc=BuybackValidationError,
        )

    # ── Concurrency guard ─────────────────────────────────────────
    # SELECT … FOR UPDATE serialises concurrent record_payment calls on
    # the same Buyback Order so two staff (or two retried clicks) cannot
    # post the same payment twice.
    locked = frappe.db.get_value(
        "Buyback Order",
        {"name": order_name},
        "name",
        for_update=True,
    )
    if not locked:
        frappe.throw(
            _("Buyback Order {0} not found.").format(order_name),
            exc=frappe.DoesNotExistError,
        )

    doc = frappe.get_doc("Buyback Order", order_name)
    require_scoped_document_action(
        doc, "payment_operation_roles", _("record a Buyback payment")
    )

    # ── Idempotency on (method, reference) ───────────────────────
    if ref:
        for existing in (doc.payments or []):
            if (existing.payment_method or "").strip() == method and (
                existing.transaction_reference or ""
            ).strip() == ref:
                # Same reference already recorded → return existing state
                # instead of double-posting. Safe replay.
                return {
                    "name": doc.name,
                    "total_paid": doc.total_paid,
                    "payment_status": doc.payment_status,
                    "status": doc.status,
                    "duplicate": True,
                }

    doc.append(
        "payments",
        {
            "payment_method": method,
            "amount": amt,
            "transaction_reference": ref or None,
            "payment_date": frappe.utils.now_datetime(),
        },
    )
    doc.flags.ch_evidence_update_authorized = True
    doc._refresh_lifecycle_evidence()
    doc.save()

    if doc.payment_status == "Paid":
        doc.mark_ready_to_pay()
        doc.mark_paid()

    return {
        "name": doc.name,
        "total_paid": doc.total_paid,
        "payment_status": doc.payment_status,
        "status": doc.status,
    }


# ── Step 9: Close Order ──────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def close_order(order_name: str) -> dict:
    """Close a fully paid buyback order."""
    doc = frappe.get_doc("Buyback Order", order_name)
    require_scoped_document_action(
        doc, "order_operation_roles", _("close a Buyback order")
    )
    doc.close()
    return {"name": doc.name, "status": doc.status}


# ── Exchange Endpoints (DEPRECATED — use select_settlement_type) ──


@frappe.whitelist(methods=["POST"])
def create_exchange(
    buyback_order: str,
    customer: str,
    mobile_no: str,
    store: str,
    old_item: str,
    new_item: str,
    buyback_amount: float | str,
    new_device_price: float | str,
    exchange_discount: float | str = 0,
    old_imei_serial: str | None = None,
    new_imei_serial: str | None = None,
    old_condition_grade: str | None = None,
) -> dict:
    """DEPRECATED: Create a Buyback Exchange Order.

    Use select_settlement_type(order_name, 'Exchange', new_item, new_device_price) instead.
    This endpoint is kept for backward compatibility only.
    """
    import warnings
    warnings.warn(
        "create_exchange is deprecated. Use select_settlement_type on the Buyback Order.",
        DeprecationWarning,
        stacklevel=2,
    )
    require_configured_role("exchange_creation_roles", action=_("create Buyback exchanges"))
    frappe.has_permission("Buyback Exchange Order", ptype="create", throw=True)
    mobile_no = validate_indian_phone(mobile_no, "Mobile No")
    source_order = frappe.get_doc("Buyback Order", buyback_order)
    source_order.check_permission("read")
    assert_buyback_scope(store=source_order.store, company=source_order.company)
    if (
        source_order.customer != customer
        or source_order.store != store
        or source_order.item != old_item
        or abs(flt(source_order.final_price) - flt(buyback_amount)) > 0.01
    ):
        frappe.throw(
            _("Exchange customer, store, old item and Buyback amount must match the source order."),
            frappe.PermissionError,
        )
    new_item_doc = frappe.get_doc("Item", new_item)
    new_item_doc.check_permission("read")

    doc = frappe.get_doc(
        {
            "doctype": "Buyback Exchange Order",
            "buyback_order": buyback_order,
            "customer": customer,
            "mobile_no": mobile_no,
            "store": store,
            "old_item": old_item,
            "old_imei_serial": old_imei_serial,
            "old_condition_grade": old_condition_grade,
            "buyback_amount": flt(buyback_amount),
            "new_item": new_item,
            "new_imei_serial": new_imei_serial,
            "new_device_price": flt(new_device_price),
            "exchange_discount": flt(exchange_discount),
        }
    )
    doc.insert()
    doc.submit()

    return {
        "name": doc.name,
        "exchange_id": doc.exchange_id,
        "status": doc.status,
        "amount_to_pay": doc.amount_to_pay,
    }


@frappe.whitelist(methods=["POST"])
def advance_exchange(exchange_name: str, action: str) -> dict:
    """Advance an exchange order through its workflow.

    Args:
        action: one of 'deliver', 'receive', 'inspect', 'settle', 'close'
    """
    doc = frappe.get_doc("Buyback Exchange Order", exchange_name)
    require_scoped_document_action(
        doc, "exchange_creation_roles", _("advance a Buyback exchange")
    )

    actions = {
        "deliver": doc.deliver_new_device,
        "receive": doc.receive_old_device,
        "inspect": doc.inspect_old_device,
        "settle": doc.settle,
        "close": doc.close,
    }

    if action not in actions:
        frappe.throw(
            _("Invalid action: {0}").format(action),
            exc=BuybackValidationError,
        )

    actions[action]()

    return {"name": doc.name, "exchange_id": doc.exchange_id, "status": doc.status}


# ── Master Data Lookups (for mobile app) ─────────────────────────


@frappe.whitelist(methods=["POST"])
def submit_mobile_diagnostic(
    mobile_no: str,
    item_code: str,
    diagnostic_results: str,
    store: str | None = None,
    imei_serial: str | None = None,
    brand: str | None = None,
    item_group: str | None = None,
    external_diagnostic_id: str | None = None,
) -> dict:
    """
    Receive diagnostic data from the mobile diagnostic app.

    Creates a Buyback Inspection record (without requiring a quote first).
    The store agent can later perform a physical inspection and create a
    quote based on the combined diagnostic + physical findings.

    Args:
        mobile_no: Customer's mobile number (used to look up or identify customer)
        item_code: The device item code
        diagnostic_results: JSON list of diagnostic test results, e.g.
            [{"test": "Battery Health", "code": "BATT", "result": "85%", "status": "Pass"},
             {"test": "Screen Touch", "code": "TOUCH", "result": "OK", "status": "Pass"}]
        store: Optional Warehouse name where device will be physically brought
        imei_serial: Device IMEI or serial number
        brand: Device brand
        item_group: Device category
        external_diagnostic_id: Reference ID from the mobile diagnostic app

    Returns:
        dict with inspection name, inspection_id, status
    """
    require_configured_role(
        "assessment_operation_roles", action=_("submit a mobile Buyback diagnostic")
    )
    frappe.has_permission("Buyback Inspection", ptype="create", throw=True)
    frappe.has_permission("Item", ptype="read", throw=True)
    mobile_no = validate_indian_phone(mobile_no, "Mobile No")

    diag_list = json.loads(diagnostic_results) if isinstance(diagnostic_results, str) else diagnostic_results
    if not isinstance(diag_list, list):
        frappe.throw(_("Diagnostic results must be a list."))
    diagnostic_limit = min(get_int_setting("max_diagnostic_rows", 100), 500)
    if not diag_list or len(diag_list) > diagnostic_limit:
        frappe.throw(_("Diagnostic results must contain between 1 and {0} rows.").format(diagnostic_limit))
    if any(not isinstance(row, dict) for row in diag_list):
        frappe.throw(_("Every diagnostic result must be an object."))

    # Look up customer by mobile number
    customer = frappe.db.get_value("Customer", {"mobile_no": mobile_no}, "name")
    customer_name = None
    if customer:
        customer_name = frappe.db.get_value("Customer", customer, "customer_name")

    # Resolve store
    store_name = None
    company = None
    if store:
        store_doc = frappe.get_doc("Warehouse", store)
        store_doc.check_permission("read")
        store_name = store
        company = store_doc.company
    if not store_name and not is_privileged_user():
        frappe.throw(_("Store is required."), frappe.PermissionError)
    assert_buyback_scope(warehouse=store_name, company=company)

    # Build inspection result rows from diagnostic data
    result_rows = []
    for idx, d in enumerate(diag_list, 1):
        result_rows.append({
            "checklist_item": d.get("test", f"Diagnostic Test {idx}"),
            "check_code": d.get("code", f"DIAG-{idx}"),
            "check_type": "Pass/Fail",
            "result": d.get("status", d.get("result", "N/A")),
            "notes": d.get("result", ""),
        })

    doc = frappe.get_doc({
        "doctype": "Buyback Inspection",

        "diagnostic_source": "Mobile App",
        "mobile_diagnostic_id": external_diagnostic_id or "",
        "customer": customer,
        "customer_name": customer_name,
        "mobile_no": mobile_no,
        "store": store_name,
        "company": company,
        "item": item_code,
        "item_name": frappe.db.get_value("Item", item_code, "item_name"),
        "imei_serial": imei_serial,
        "diagnostic_data": json.dumps(diag_list, indent=2),
        "results": result_rows,
        "remarks": f"Auto-created from mobile diagnostic app",
    })
    doc.insert()

    # Calculate estimated price from diagnostic answers using the pricing engine
    estimated_price = 0
    try:
        from buyback.buyback.pricing.engine import calculate_estimated_price
        # Map diagnostic results to question-style responses for pricing
        resp_for_pricing = _map_diagnostic_to_responses(diag_list)
        pricing = calculate_estimated_price(
            item_code=item_code,
            grade=None,
            responses=resp_for_pricing,
            brand=brand,
            item_group=item_group,
        )
        estimated_price = pricing.get("estimated_price", 0)
    except (ValueError, KeyError, frappe.ValidationError, frappe.DoesNotExistError):
        frappe.log_error(
            title=f"Mobile diagnostic pricing failed for {doc.name}",
        )

    # Update Serial No status to "Quoted" if IMEI provided
    if imei_serial:
        from buyback.serial_no_utils import update_serial_buyback_status
        update_serial_buyback_status(
            imei_serial,
            status="Under Inspection",
            comment=f"Mobile diagnostic submitted — Inspection {doc.name}",
        )

    return {
        "name": doc.name,
        "inspection_id": doc.inspection_id,
        "status": doc.status,
        "diagnostic_source": doc.diagnostic_source,
        "customer": doc.customer,
        "customer_found": bool(customer),
        "results_count": len(result_rows),
        "estimated_price": estimated_price,
    }


@frappe.whitelist()
def get_inspections_by_phone(mobile_no: str) -> list[dict]:
    """
    Look up all Buyback Inspections for a given mobile number.

    Used by store agents to find pending mobile diagnostics that need
    physical inspection.
    """
    require_configured_role("customer_lookup_roles", action=_("find customer inspections"))
    for doctype in ("Buyback Inspection", "Customer", "Item"):
        if not frappe.has_permission(doctype, ptype="read"):
            frappe.throw(
                _("You do not have read permission for {0}.").format(doctype),
                frappe.PermissionError,
            )
    mobile_no = validate_indian_phone(mobile_no, "Mobile No")
    scope_sql, scope_params = build_buyback_scope_sql(
        store_field="bi.store",
        company_field="bi.company",
        prefix="inspection_lookup",
    )
    params = {
        "mobile_no": mobile_no,
        "limit": get_int_setting("customer_lookup_limit", 50),
        **scope_params,
    }
    return frappe.db.sql(
        f"""
        SELECT
            bi.name, bi.inspection_id, bi.customer, bi.customer_name,
            bi.item, bi.item_name, bi.status, bi.diagnostic_source,
            bi.mobile_diagnostic_id, bi.creation
        FROM `tabBuyback Inspection` bi
        WHERE bi.mobile_no = %(mobile_no)s
          AND ({scope_sql})
        ORDER BY bi.creation DESC
        LIMIT %(limit)s
        """,
        params,
        as_dict=True,
    )


@frappe.whitelist(methods=["POST"])
def submit_imei_validation(order_name: str, status: str, screenshot: str | None = None,
                            remarks: str | None = None) -> dict:
    """Record the manual Sanchar Saathi (CEIR) IMEI check result for a Buyback Order.

    There is no public API for the government CEIR registry, so store staff
    log into ceir.sancharsaathi.gov.in themselves, check the device IMEI, and
    upload a screenshot of the result here. Must be status="Verified Clean"
    before customer approval, KYC, or OTP can proceed — enforced server-side
    in BuybackOrder._validate_imei_check_before_kyc().
    """
    doc = frappe.get_doc("Buyback Order", order_name)
    doc.check_permission("write")
    return doc.submit_imei_validation(status=status, screenshot=screenshot, remarks=remarks)


@frappe.whitelist(methods=["POST"])
def verify_kyc(order_name: str) -> dict:
    """Verify KYC documents for a buyback order."""
    doc = frappe.get_doc("Buyback Order", order_name)
    require_scoped_document_action(
        doc, "order_operation_roles", _("verify Buyback KYC")
    )
    doc.verify_kyc()
    return {
        "name": doc.name,
        "kyc_verified": doc.kyc_verified,
        "kyc_verified_by": doc.kyc_verified_by,
    }


def _get_question_applicable_categories(question_name: str, legacy_category: str | None = None) -> list[str]:
    rows = frappe.get_all(
        "Buyback Question Applicable Category",
        filters={
            "parent": question_name,
            "parenttype": "Buyback Question Bank",
            "parentfield": "applies_to_categories",
        },
        pluck="item_group",
    )
    cleaned = [r for r in (rows or []) if r]
    if cleaned:
        return cleaned
    if legacy_category:
        return [legacy_category]
    return []


def _get_question_categories(question_names: list[str]) -> dict[str, set[str]]:
    """Batch-load the applicable Item Groups for Question Bank rows."""
    names = list(dict.fromkeys(name for name in question_names if name))
    if not names:
        return {}
    categories: dict[str, set[str]] = {name: set() for name in names}
    for row in frappe.get_all(
        "Buyback Question Applicable Category",
        filters={
            "parent": ["in", names],
            "parenttype": "Buyback Question Bank",
            "parentfield": "applies_to_categories",
        },
        fields=["parent", "item_group"],
    ):
        if row.item_group:
            categories.setdefault(row.parent, set()).add(row.item_group)
    return categories


def _get_options_by_question(question_names: list[str]) -> dict[str, list[dict]]:
    """Batch-load ordered options for a set of Question Bank rows."""
    names = list(dict.fromkeys(name for name in question_names if name))
    if not names:
        return {}
    options: dict[str, list[dict]] = {name: [] for name in names}
    for row in frappe.get_all(
        "Buyback Question Option",
        filters={"parent": ["in", names]},
        fields=[
            "parent", "option_label", "option_value",
            "price_impact_percent", "is_default", "idx",
        ],
        order_by="parent asc, idx asc",
    ):
        options.setdefault(row.parent, []).append({
            "option_label": row.option_label,
            "option_value": row.option_value,
            "price_impact_percent": row.price_impact_percent,
            "is_default": row.is_default,
        })
    return options


@frappe.whitelist()
def get_questions(category: str | None = None) -> list[dict]:
    """Get active questions for a category (or all)."""
    _require_app_read(_("view Buyback questions"), "Buyback Question Bank")
    questions = frappe.get_list(
        "Buyback Question Bank",
        filters={"disabled": 0},
        fields=[
            "name", "question_id", "question_text", "question_code",
            "question_type", "display_order", "is_mandatory", "applies_to_category",
        ],
        order_by="display_order asc, question_id asc",
        limit_page_length=500,
    )

    question_names = [q["name"] for q in questions]
    if category:
        categories = _get_question_categories(question_names)
        filtered = []
        for q in questions:
            applicable = categories.get(q["name"]) or (
                {q.get("applies_to_category")} if q.get("applies_to_category") else set()
            )
            # No category set means "global" question, applicable to all categories.
            if not applicable or category in applicable:
                filtered.append(q)
        questions = filtered

    options_by_question = _get_options_by_question([q["name"] for q in questions])
    for q in questions:
        q.pop("applies_to_category", None)
        q["options"] = options_by_question.get(q["name"], [])

    return questions


@frappe.whitelist()
def get_grades() -> list[dict]:
    """Get all active grades."""
    _require_app_read(_("view Buyback grades"), "Grade Master")
    return frappe.get_list(
        "Grade Master",
        filters={"disabled": 0},
        fields=["name", "grade_id", "grade_name", "description", "display_order"],
        order_by="display_order asc",
        limit_page_length=100,
    )


@frappe.whitelist()
def get_stores(
    company: str | None = None,
    buyback_enabled: int | str | None = None,
) -> list[dict]:
    """Get active stores, optionally filtered."""
    _require_app_read(_("view Buyback stores"), "Warehouse")
    filters: dict = {"disabled": 0, "is_group": 0}
    if company:
        filters["company"] = company
    if buyback_enabled:
        filters["ch_is_buyback_enabled"] = 1

    scope = get_buyback_data_scope()
    if not scope["bypass"]:
        locations = sorted(scope["warehouses"] | scope["stores"])
        companies = sorted(scope["companies"])
        if locations:
            filters["name"] = ["in", locations]
        elif companies:
            filters["company"] = ["in", companies]
        else:
            return []
        if company and company not in companies and not locations:
            frappe.throw(_("Company is outside your configured scope."), frappe.PermissionError)

    return frappe.get_list(
        "Warehouse",
        filters=filters,
        fields=[
            "name", "ch_store_id as store_id", "ch_store_code as store_code",
            "warehouse_name as store_name",
            "company", "city", "state", "pin as pincode",
        ],
        order_by="warehouse_name asc",
        limit_page_length=200,
    )


@frappe.whitelist()
def get_payment_methods() -> list[dict]:
    """Get active payment methods (standard ERPNext Mode of Payment)."""
    _require_app_read(_("view Buyback payment methods"), "Mode of Payment")
    return frappe.get_list(
        "Mode of Payment",
        filters={"enabled": 1},
        fields=["name", "mode_of_payment", "type"],
        order_by="name asc",
        limit_page_length=100,
    )


# ── Helpers ───────────────────────────────────────────────────────


def _map_diagnostic_to_responses(diag_list: list[dict]) -> list[dict]:
    """Map mobile diagnostic results to question-style responses for pricing.

    Mobile diagnostics have: {test, code, result, status}
    Pricing engine expects: {question_code, answer_value}

    We attempt to match diagnostic codes to question bank codes.
    Unmatched diagnostics are skipped (pricing engine ignores unknown codes).
    """
    codes = list(dict.fromkeys(
        str(d.get("code") or "").strip()
        for d in (diag_list or [])
        if isinstance(d, dict) and str(d.get("code") or "").strip()
    ))
    valid_codes = set(frappe.get_all(
        "Buyback Question Bank",
        filters={"question_code": ["in", codes], "disabled": 0},
        pluck="question_code",
    )) if codes else set()
    responses = []
    for d in diag_list:
        code = d.get("code", "")
        status = (d.get("status") or "N/A").lower()
        # Map Pass/Fail to yes/no answer values
        answer = "yes" if status == "pass" else "no" if status == "fail" else status
        # Only include if a matching question code exists
        if code in valid_codes:
            responses.append({"question_code": code, "answer_value": answer})
    return responses


# ── Item Search API (for mobile app) ─────────────────────────────


@frappe.whitelist()
def search_items(
    search_text: str | None = None,
    brand: str | None = None,
    item_group: str | None = None,
    category: str | None = None,
    sub_category: str | None = None,
    model: str | None = None,
    limit: int | str = 20,
) -> list[dict]:
    """Search items by brand, category, model, or free text.

    Used by the mobile app to browse/search buyback-eligible items.
    Returns items with all hierarchy IDs for API consumption.
    """
    _require_app_read(_("search Buyback items"), "Item")
    filters: dict = {"disabled": 0, "is_stock_item": 1}
    if brand:
        filters["brand"] = brand
    if item_group:
        filters["item_group"] = item_group
    if category:
        filters["ch_category"] = category
    if sub_category:
        filters["ch_sub_category"] = sub_category
    if model:
        filters["ch_model"] = model

    or_filters = {}
    if search_text:
        or_filters = {
            "item_name": ["like", f"%{search_text}%"],
            "item_code": ["like", f"%{search_text}%"],
            "ch_display_name": ["like", f"%{search_text}%"],
        }

    result_limit = min(max(int(limit or 20), 1), 50)
    return frappe.get_list(
        "Item",
        filters=filters,
        or_filters=or_filters or None,
        fields=[
            "name", "item_code", "item_name", "ch_display_name",
            "item_group", "brand", "ch_category", "ch_sub_category",
            "ch_model", "ch_brand_id", "ch_manufacturer_id",
            "ch_category_id", "ch_sub_category_id", "ch_model_id",
            "ch_item_group_id", "image",
        ],
        order_by="item_name asc",
        limit_page_length=result_limit,
    )


# ── Lookup by Phone (Quotes + Orders + Assessments) ─────────────


@frappe.whitelist()
def get_assessments_by_phone(mobile_no: str) -> list[dict]:
    """Look up Buyback Assessments within the caller's assigned stores."""
    require_configured_role("customer_lookup_roles", action=_("look up buyback customers"))
    frappe.has_permission("Buyback Assessment", "read", throw=True)
    mobile_no = validate_indian_phone(mobile_no, "Mobile No")
    return _query_assessments_by_phone(mobile_no, enforce_user_scope=True)


def _query_assessments_by_phone(mobile_no: str, *, enforce_user_scope: bool, limit: int = 50) -> list[dict]:
    scope_sql, scope_params = ("1=1", {})
    if enforce_user_scope:
        scope_sql, scope_params = build_buyback_scope_sql(
            store_field="ba.store", company_field="ba.company", prefix="assessment_phone"
        )
    limit = min(max(int(limit or 1), 1), get_int_setting("customer_lookup_limit", 50))
    return frappe.db.sql(
        f"""SELECT ba.name, ba.assessment_id, ba.source, ba.item,
                   ba.estimated_grade, ba.estimated_price, ba.status,
                   ba.buyback_inspection, ba.expires_on, ba.creation
              FROM `tabBuyback Assessment` ba
             WHERE ba.mobile_no = %(mobile_no)s AND {scope_sql}
             ORDER BY ba.creation DESC
             LIMIT {limit}""",
        {"mobile_no": mobile_no, **scope_params},
        as_dict=True,
    )


@frappe.whitelist()
def get_orders_by_phone(mobile_no: str) -> list[dict]:
    """Look up all Buyback Orders for a given mobile number.

    Used by store agents to find existing orders for a customer.
    """
    require_configured_role("customer_lookup_roles", action=_("look up buyback customers"))
    frappe.has_permission("Buyback Order", "read", throw=True)
    mobile_no = validate_indian_phone(mobile_no, "Mobile No")
    return _query_orders_by_phone(mobile_no, enforce_user_scope=True)


def _query_orders_by_phone(mobile_no: str, *, enforce_user_scope: bool, limit: int = 50) -> list[dict]:
    scope_sql, scope_params = ("1=1", {})
    if enforce_user_scope:
        scope_sql, scope_params = build_buyback_scope_sql(
            store_field="bo.store", company_field="bo.company", prefix="order_phone"
        )
    limit = min(max(int(limit or 1), 1), get_int_setting("customer_lookup_limit", 50))
    return frappe.db.sql(
        f"""SELECT bo.name, bo.order_id, bo.customer, bo.customer_name,
                   bo.item, bo.item_name, bo.imei_serial, bo.final_price,
                   bo.condition_grade, bo.status, bo.payment_status,
                   bo.workflow_state, bo.creation
              FROM `tabBuyback Order` bo
             WHERE bo.mobile_no = %(mobile_no)s AND {scope_sql}
             ORDER BY bo.creation DESC
             LIMIT {limit}""",
        {"mobile_no": mobile_no, **scope_params},
        as_dict=True,
    )


def get_customer_portal_buyback_history(mobile_no: str, limit: int = 20) -> dict:
    """Private helper for an already OTP-authenticated customer portal session."""
    mobile_no = validate_indian_phone(mobile_no, "Mobile No")
    limit = min(max(int(limit or 1), 1), 20)
    return {
        "assessments": _query_assessments_by_phone(
            mobile_no, enforce_user_scope=False, limit=limit
        ),
        "orders": _query_orders_by_phone(mobile_no, enforce_user_scope=False, limit=limit),
    }


# ── IMEI History API ────────────────────────────────────────────


@frappe.whitelist()
def get_imei_history(imei: str) -> dict:
    """Get consolidated buyback history for an IMEI/Serial No.

    Queries across Serial No, Quotes, Inspections, Orders, Exchanges
    and returns a unified timeline view. Reuses ERPNext's Serial No
    DocType — no separate IMEI History DocType needed.
    """
    require_configured_role("imei_history_roles", action=_("view IMEI history"))
    from buyback.serial_no_utils import get_imei_history as _get_history
    return _get_history(imei)


# ── Customer Approval Page Data ─────────────────────────────────


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60, methods=["GET", "POST"], ip_based=True)
def get_buyback_approval_details(token: str) -> dict:
    """Get buyback order details for the customer-facing approval page.

    The token is a hash stored on the order — no login required.
    Customer sees: item details, price, store, photos, and can
    trigger OTP verification from the approval page.
    """
    order_name = _resolve_token(token)
    order = frappe.get_doc("Buyback Order", order_name)

    return {
        "name": order.name,
        "order_id": order.order_id,
        "customer_name": order.customer_name,
        "item_name": frappe.db.get_value("Item", order.item, "item_name") or order.item,
        "brand": order.brand,
        "imei_serial": order.imei_serial,
        "condition_grade": frappe.db.get_value(
            "Grade Master", order.condition_grade, "grade_name"
        ) if order.condition_grade else "",
        "final_price": order.final_price,
        "store_name": frappe.db.get_value(
            "Warehouse", order.store, "warehouse_name"
        ) if order.store else "",
        "status": order.status,
        "device_photo_front": order.device_photo_front,
        "device_photo_back": order.device_photo_back,
        "otp_verified": order.otp_verified,
        "warranty_status": order.warranty_status,
        "mobile_no_masked": _mask_phone(order.mobile_no),
        "customer_payout_mode": order.customer_payout_mode,
        "customer_cash_receiver_name_masked": _mask_name(order.customer_cash_receiver_name),
        "customer_upi_id_masked": _mask_upi(order.customer_upi_id),
        "customer_bank_account_holder_masked": _mask_name(order.customer_bank_account_holder),
        "customer_bank_account_number_masked": _mask_identifier(order.customer_bank_account_number),
        "customer_bank_ifsc_masked": _mask_identifier(order.customer_bank_ifsc, visible=3),
        "customer_bank_name": order.customer_bank_name,
        "customer_payout_updated_at": str(order.customer_payout_updated_at) if order.customer_payout_updated_at else "",
        "customer_id_type": order.customer_id_type,
        "customer_id_number_masked": _mask_identifier(order.customer_id_number),
        "kyc_verified": order.kyc_verified,
        "kyc_verified_at": str(order.kyc_verified_at) if order.kyc_verified_at else "",
    }


# ── Diagnostic Comparison ───────────────────────────────────────


@frappe.whitelist()
def get_diagnostic_comparison(inspection_name: str) -> dict:
    """Get a normalized side-by-side comparison of mobile diagnostic
    answers vs in-store inspection results.

    Returns a list of items, each with:
      test_name, code, mobile_result, mobile_status, store_result, match
    """
    _require_app_read(_("view Buyback diagnostic comparisons"), "Buyback Inspection")
    doc = frappe.get_doc("Buyback Inspection", inspection_name)
    doc.check_permission("read")
    assert_buyback_scope(store=doc.store, company=doc.company)

    comparison = []

    # Parse mobile diagnostic data
    mobile_results = {}
    if doc.diagnostic_data:
        try:
            diag_list = json.loads(doc.diagnostic_data)
            for d in diag_list:
                code = d.get("code", d.get("test", ""))
                mobile_results[code] = {
                    "test_name": d.get("test", code),
                    "result": d.get("result", ""),
                    "status": d.get("status", "N/A"),
                }
        except (json.JSONDecodeError, TypeError):
            pass

    # Map in-store results by check_code
    store_results = {}
    for row in (doc.results or []):
        store_results[row.check_code] = {
            "test_name": row.checklist_item,
            "result": row.result,
            "notes": row.notes,
        }

    # Build unified comparison
    all_codes = set(list(mobile_results.keys()) + list(store_results.keys()))
    for code in sorted(all_codes):
        mob = mobile_results.get(code, {})
        sto = store_results.get(code, {})
        mob_status = (mob.get("status") or "N/A").lower()
        sto_result = (sto.get("result") or "N/A").lower()

        # Determine match: both Pass/OK = match, both Fail = match, else mismatch
        match = None
        if mob and sto:
            match = (
                (mob_status in ("pass", "ok") and sto_result in ("pass", "ok"))
                or (mob_status in ("fail",) and sto_result in ("fail",))
            )

        comparison.append({
            "code": code,
            "test_name": mob.get("test_name") or sto.get("test_name") or code,
            "mobile_result": mob.get("result", ""),
            "mobile_status": mob.get("status", ""),
            "store_result": sto.get("result", ""),
            "store_notes": sto.get("notes", ""),
            "match": match,
            "has_mobile": bool(mob),
            "has_store": bool(sto),
        })

    return {
        "inspection": inspection_name,
        "diagnostic_source": doc.diagnostic_source,
        "total_tests": len(comparison),
        "matches": sum(1 for c in comparison if c["match"] is True),
        "mismatches": sum(1 for c in comparison if c["match"] is False),
        "comparison": comparison,
    }


# ── Question Bank Options ─────────────────────────────────────────

@frappe.whitelist()
def get_question_options(question_name: str) -> list:
    """Return the answer options for a Buyback Question Bank entry.

    Used by the Assessment form to populate answer dropdowns.
    """
    if not question_name:
        return []

    _require_app_read(_("view Buyback question options"), "Buyback Question Bank")
    frappe.get_doc("Buyback Question Bank", question_name).check_permission("read")

    options = frappe.get_all(
        "Buyback Question Option",
        filters={"parent": question_name},
        fields=["option_value", "option_label", "price_impact_percent"],
        order_by="idx asc",
        limit_page_length=50,
    )

    # TC_058: Automated test flows should display Yes/No (not Pass/Fail/Partial)
    # while staying compatible with existing Question Bank data.
    diagnosis_type = frappe.db.get_value(
        "Buyback Question Bank", question_name, "diagnosis_type"
    )
    if diagnosis_type == "Automated Test":
        return _normalize_automated_test_options(options)

    return options


def _normalize_automated_test_options(options: list[dict]) -> list[dict]:
    """Normalize automated-test options to Yes/No.

    Legacy Question Bank rows may still store Pass/Fail/Partial values.
    Map these to Yes/No for UI while preserving price impact behavior.
    """
    if not options:
        return []

    by_value = {
        (o.get("option_value") or "").strip().lower(): {
            "option_value": o.get("option_value"),
            "option_label": o.get("option_label"),
            "price_impact_percent": flt(o.get("price_impact_percent") or 0),
        }
        for o in options
    }

    if "yes" in by_value and "no" in by_value:
        return [
            {
                "option_value": "Yes",
                "option_label": by_value["yes"].get("option_label") or "Yes",
                "price_impact_percent": by_value["yes"].get("price_impact_percent") or 0,
            },
            {
                "option_value": "No",
                "option_label": by_value["no"].get("option_label") or "No",
                "price_impact_percent": by_value["no"].get("price_impact_percent") or 0,
            },
        ]

    # Legacy Pass/Fail/Partial mapping
    # Yes = defect exists → use Fail (or Partial) impact
    # No  = no defect    → use Pass impact (usually 0)
    fail_impact = by_value.get("fail", {}).get("price_impact_percent", 0)
    partial_impact = by_value.get("partial", {}).get("price_impact_percent", 0)
    pass_impact = by_value.get("pass", {}).get("price_impact_percent", 0)

    yes_impact = max(fail_impact, partial_impact) if (fail_impact or partial_impact) else 0
    no_impact = pass_impact

    return [
        {
            "option_value": "Yes",
            "option_label": "Yes",
            "price_impact_percent": yes_impact,
        },
        {
            "option_value": "No",
            "option_label": "No",
            "price_impact_percent": no_impact,
        },
    ]


# ── Reference Price Lookup ────────────────────────────────────────

@frappe.whitelist()
def get_reference_prices(item_code: str) -> dict:
    """Return market price and vendor price from Buyback Price Master.

    Used by the Assessment form to show price cards once device details
    are filled.
    """
    if not item_code:
        return {"market_price": 0, "vendor_price": 0}

    require_configured_role("assessment_operation_roles", action=_("view reference prices"))
    item_doc = frappe.get_doc("Item", item_code)
    for doctype, doc in (("Item", item_doc), ("Buyback Price Master", None)):
        if not frappe.has_permission(doctype, ptype="read", doc=doc):
            frappe.throw(
                _("You do not have read permission for {0}.").format(doctype),
                frappe.PermissionError,
            )

    bpm = frappe.db.get_value(
        "Buyback Price Master",
        {"item_code": item_code},
        ["current_market_price", "vendor_price"],
        as_dict=True,
    )

    if not bpm:
        return {"market_price": 0, "vendor_price": 0}

    return {
        "market_price": bpm.current_market_price or 0,
        "vendor_price": bpm.vendor_price or 0,
    }


# ── Live Estimate Calculator ─────────────────────────────────────

@frappe.whitelist()
def calculate_live_estimate(
    item_code: str,
    warranty_status: str = None,
    device_age_months: str = None,
    diagnostic_tests: str = None,
    responses: str = None,
    brand: str = None,
    item_group: str = None,
    is_phone_dead: int = 0, 
) -> dict:
    """Calculate estimated price + grade."""
    import json

    _require_app_read(
        _("calculate a Buyback estimate"),
        "Item",
        "Grade Master",
        "Buyback Price Master",
        "Buyback Pricing Rule",
    )
    frappe.get_doc("Item", item_code).check_permission("read")

    diag_data = json.loads(diagnostic_tests or "[]")
    resp_data = json.loads(responses or "[]")
    if not isinstance(diag_data, list) or not isinstance(resp_data, list):
        frappe.throw(_("Diagnostic tests and responses must be lists."))
    input_limit = min(get_int_setting("max_diagnostic_rows", 100), 500)
    if len(diag_data) > input_limit or len(resp_data) > input_limit:
        frappe.throw(_("Estimate inputs may contain at most {0} rows each.").format(input_limit))

    provisional_grade_id = frappe.db.get_value(
        "Grade Master", {"grade_name": "A"}, "name"
    ) or ""

    from buyback.buyback.pricing.engine import calculate_estimated_price

    result = calculate_estimated_price(
        item_code=item_code,
        grade=provisional_grade_id,
        warranty_status=warranty_status,
        device_age_months=device_age_months,
        responses=resp_data,
        diagnostic_tests=diag_data,
        brand=brand,
        item_group=item_group,
        is_phone_dead=bool(int(is_phone_dead or 0)),
    )

    if not result:
        result = {
            "base_price": 0,
            "estimated_price": 0,
            "deductions": [],
            "total_deductions": 0,
            "grade_letter": "A",
        }

    final_grade = result.get("grade_letter") or "A"
    final_grade_id = frappe.db.get_value(
        "Grade Master", {"grade_name": final_grade}, "name"
    ) or ""

    result["grade"] = final_grade
    result["grade_id"] = final_grade_id
    return result


#updated Auto determine grade :

def _auto_determine_grade(diagnostic_tests: list, device_age_months: str = None) -> str:
    """Deprecated. Returns provisional 'A'. Real grade comes from engine."""
    return "A"


# ── Item Question Map Helper ──────────────────────────────────────


def _get_mapped_question_names(item_code: str, diagnosis_type: str) -> list[str] | None:
    """Return ordered list of Question Bank names mapped to an item.

    Lookup order (independent per question/test type):
      1. Model-level map (map_type='Model', item_code=item_code)
      2. Subcategory-level map (map_type='Subcategory', item_group=item's group)
      3. None — no mapping at any level, caller falls back to all

    If a model-level map exists but has no rows for this type, the lookup
    continues to the subcategory level instead of returning empty.

    Returns:
        list[str] of Buyback Question Bank names, or None if no mapping exists.
        None means "no mapping configured — caller may fall back to all".
    """
    item_group = frappe.db.get_value("Item", item_code, "item_group")

    # Build candidate maps: model-level first, then subcategory
    candidates = []

    item_map = frappe.db.get_value(
        "Buyback Item Question Map",
        {"map_type": "Model Override", "item_code": item_code, "disabled": 0},
        "name",
    )
    if item_map:
        candidates.append(item_map)

    if item_group:
        group_map = frappe.db.get_value(
            "Buyback Item Question Map",
            {"map_type": "Subcategory Default", "item_group": item_group, "disabled": 0},
            "name",
        )
        if group_map:
            candidates.append(group_map)

    if not candidates:
        return None  # No mapping exists at any level

    # Try each candidate; use the first one that has rows for this type
    for map_name in candidates:
        if diagnosis_type == "Automated Test":
            rows = frappe.get_all(
                "Buyback Item Test Map Detail",
                filters={"parent": map_name},
                fields=["test as question", "display_order"],
                order_by="display_order asc, idx asc",
            )
        else:
            rows = frappe.get_all(
                "Buyback Item Question Map Detail",
                filters={"parent": map_name},
                fields=["question", "display_order"],
                order_by="display_order asc, idx asc",
            )

        if rows:
            return [r.question for r in rows]

    # Mappings exist but none have rows for this type
    return []


# ── Diagnostic Test Loader ────────────────────────────────────────


@frappe.whitelist()
def get_diagnostic_tests_for_item(item_code: str) -> list:
    """Return enabled automated diagnostic tests applicable to an item.

    Uses Buyback Item Question Map to filter tests.
    Falls back to ALL enabled Automated Tests if no mapping exists.

    Returns:
        list[dict]: [{"name": "BQB-...", "test_code": "...", "test_name": "...",
                       "options": [{"option_value": "Pass", ...}, ...]}]
    """
    if not item_code:
        return []
    _require_app_read(_("view Buyback diagnostic tests"), "Item", "Buyback Question Bank")
    frappe.get_doc("Item", item_code).check_permission("read")

    mapped_names = _get_mapped_question_names(item_code, "Automated Test")

    filters = {"diagnosis_type": "Automated Test", "disabled": 0}
    if mapped_names is not None:
        if not mapped_names:
            return []  # Mapping exists but has no automated tests
        filters["name"] = ("in", mapped_names)

    tests = frappe.get_list(
        "Buyback Question Bank",
        filters=filters,
        fields=["name", "question_code", "question_text"],
        order_by="idx asc, name asc",
        limit_page_length=500,
    )

    # If mapped, preserve mapping order
    if mapped_names is not None:
        order_map = {n: i for i, n in enumerate(mapped_names)}
        tests.sort(key=lambda t: order_map.get(t.name, 999))

    options_by_question = _get_options_by_question([t.name for t in tests])
    result = []
    for t in tests:
        options = options_by_question.get(t.name, [])
        normalized_options = _normalize_automated_test_options(options)
        result.append({
            "name": t.name,
            "test_code": t.question_code,
            "test_name": t.question_text,
            "options": [
                {
                    "value": o.get("option_value"),
                    "label": o.get("option_label") or o.get("option_value"),
                    "impact": o.get("price_impact_percent") or 0,
                }
                for o in normalized_options
            ],
        })

    return result


@frappe.whitelist()
def get_customer_questions_for_item(item_code: str) -> list:
    """Return enabled customer questions applicable to an item.

    Uses Buyback Item Question Map to filter questions.
    Falls back to ALL enabled Customer Questions if no mapping exists.

    Returns:
        list[dict]: [{"name": "BQB-...", "question_code": "...",
                      "question_text": "...",
                      "options": [{"value": ..., "label": ..., "impact": ...}]}]
    """
    if not item_code:
        return []
    _require_app_read(_("view Buyback customer questions"), "Item", "Buyback Question Bank")
    frappe.get_doc("Item", item_code).check_permission("read")

    mapped_names = _get_mapped_question_names(item_code, "Customer Question")

    filters = {"diagnosis_type": "Customer Question", "disabled": 0}
    if mapped_names is not None:
        if not mapped_names:
            return []  # Mapping exists but has no customer questions
        filters["name"] = ("in", mapped_names)

    questions = frappe.get_list(
        "Buyback Question Bank",
        filters=filters,
        fields=["name", "question_code", "question_text"],
        order_by="idx asc, name asc",
        limit_page_length=500,
    )

    # If mapped, preserve mapping order
    if mapped_names is not None:
        order_map = {n: i for i, n in enumerate(mapped_names)}
        questions.sort(key=lambda q: order_map.get(q.name, 999))

    options_by_question = _get_options_by_question([q.name for q in questions])
    result = []
    for q in questions:
        options = options_by_question.get(q.name, [])
        result.append({
            "name": q.name,
            "question_code": q.question_code,
            "question_text": q.question_text,
            "options": [
                {
                    "value": o.get("option_value"),
                    "label": o.get("option_label") or o.get("option_value"),
                    "impact": o.get("price_impact_percent") or 0,
                }
                for o in options
            ],
        })

    return result


# ── Exchange → Invoice auto-mapping ──────────────────────────────


@frappe.whitelist()
def get_open_exchange_orders_for_customer(
    customer: str,
    mobile_no: str | None = None,
) -> list[dict]:
    """Return open (unlinked) Buyback Exchange Orders for a customer.

    Used by the POS / billing UI to show available trade-in credits when
    creating a Sales Invoice.  Only returns orders that:
      - belong to the customer (or match mobile_no)
      - are submitted (docstatus=1)
      - have not yet been linked to a Sales Invoice
      - are in a status that allows crediting (New Device Delivered,
        Awaiting Pickup, Old Device Received, Inspected, Settled)

    Args:
        customer: ERPNext Customer link value
        mobile_no: Optional mobile number (fallback lookup when customer link
                   is not yet set)

    Returns:
        List of dicts with exchange order details and amount to credit.
    """
    require_configured_role("app_access_roles", action=_("view Buyback exchanges"))
    frappe.has_permission("Buyback Exchange Order", ptype="read", throw=True)
    customer_doc = frappe.get_doc("Customer", customer) if customer else None
    if customer_doc:
        customer_doc.check_permission("read")

    CREDITABLE_STATUSES = (
        "New Device Delivered",
        "Awaiting Pickup",
        "Old Device Received",
        "Inspected",
        "Settled",
    )

    filters: dict = {
        "docstatus": 1,
        "sales_invoice": ["is", "not set"],
        "status": ["in", list(CREDITABLE_STATUSES)],
    }
    if customer:
        filters["customer"] = customer
    elif mobile_no:
        filters["mobile_no"] = validate_indian_phone(mobile_no, "Mobile No")
    else:
        frappe.throw(
            _("Provide either customer or mobile_no."),
            exc=BuybackValidationError,
            title=_("Exchange Lookup Error"),
        )

    scope = get_buyback_data_scope()
    if not scope["bypass"]:
        locations = sorted(scope["stores"] | scope["warehouses"])
        companies = sorted(scope["companies"])
        if locations:
            filters["store"] = ["in", locations]
        elif companies:
            filters["company"] = ["in", companies]
        else:
            return []

    orders = frappe.get_list(
        "Buyback Exchange Order",
        filters=filters,
        fields=[
            "name", "exchange_id", "customer", "mobile_no",
            "old_item", "old_item_name", "old_imei_serial",
            "old_condition_grade", "buyback_amount",
            "new_item", "new_item_name", "new_imei_serial",
            "new_device_price", "exchange_discount", "amount_to_pay",
            "status", "store",
        ],
        order_by="exchange_id desc",
        limit=10,
    )
    return orders


@frappe.whitelist(methods=["POST"])
def apply_exchange_to_invoice(
    exchange_order: str,
    sales_invoice: str,
) -> dict:
    """Link a Buyback Exchange Order to a Sales Invoice and return the
    buyback amount to apply as a trade-in credit.

    Enforces:
      1. Customer on exchange order == customer on Sales Invoice
         → prevents staff applying Exchange Order from Customer A to
           Customer B's bill
      2. Exchange order not already applied to another invoice
      3. Sales Invoice not already linked to a different exchange order
      4. Exchange order in a creditable status

    This is the **only** authorised way to link an exchange to a sale.
    Direct field edits on either side will fail the validation hook.

    Returns:
        dict with exchange_order, sales_invoice, buyback_amount, amount_to_pay
    """
    exo = frappe.get_doc("Buyback Exchange Order", exchange_order)
    si  = frappe.get_doc("Sales Invoice", sales_invoice)
    require_configured_role("exchange_creation_roles", action=_("apply Buyback exchange credit"))
    exo.check_permission("write")
    si.check_permission("write")
    assert_buyback_scope(store=exo.store, company=exo.company)
    if exo.company != si.company:
        frappe.throw(_("Exchange Order and Sales Invoice companies must match."), frappe.PermissionError)
    if si.docstatus != 0:
        frappe.throw(_("Exchange credit can only be applied to a Draft Sales Invoice."))
    frappe.db.get_value("Buyback Exchange Order", exo.name, "name", for_update=True)
    frappe.db.get_value("Sales Invoice", si.name, "name", for_update=True)
    exo.reload()
    si.reload()

    # ── Guard 1: customer must match ──────────────────────────────
    if exo.customer != si.customer:
        frappe.throw(
            _(
                "Exchange Order {0} belongs to customer <b>{1}</b> but "
                "Sales Invoice {2} is for customer <b>{3}</b>. "
                "Cannot apply exchange credit across customers."
            ).format(
                frappe.bold(exo.name),
                exo.customer,
                frappe.bold(si.name),
                si.customer,
            ),
            exc=BuybackValidationError,
            title=_("Customer Mismatch"),
        )

    # ── Guard 2: exchange order not already used ──────────────────
    if exo.sales_invoice:
        frappe.throw(
            _(
                "Exchange Order {0} has already been applied to "
                "Sales Invoice {1}. Each exchange order can only be "
                "used once."
            ).format(frappe.bold(exo.name), frappe.bold(exo.sales_invoice)),
            exc=BuybackValidationError,
            title=_("Exchange Already Used"),
        )

    # ── Guard 3: invoice not already linked to a different order ──
    existing = frappe.db.get_value("Sales Invoice", sales_invoice, "ch_exchange_order")
    if existing and existing != exchange_order:
        frappe.throw(
            _(
                "Sales Invoice {0} is already linked to Exchange Order {1}."
            ).format(frappe.bold(si.name), frappe.bold(existing)),
            exc=BuybackValidationError,
            title=_("Invoice Already Has Exchange"),
        )

    # ── Guard 4: creditable status ────────────────────────────────
    CREDITABLE_STATUSES = {
        "New Device Delivered", "Awaiting Pickup",
        "Old Device Received", "Inspected", "Settled",
    }
    if exo.status not in CREDITABLE_STATUSES:
        frappe.throw(
            _(
                "Exchange Order {0} is in status <b>{1}</b> which does not "
                "allow crediting. Allowed statuses: {2}."
            ).format(
                frappe.bold(exo.name),
                exo.status,
                ", ".join(sorted(CREDITABLE_STATUSES)),
            ),
            exc=BuybackValidationError,
            title=_("Invalid Exchange Status"),
        )

    buyback_amount = flt(exo.buyback_amount)

    # ── Apply linkage (bypass submit lock — these are audit fields) ─
    frappe.db.set_value(
        "Buyback Exchange Order",
        exchange_order,
        {
            "sales_invoice": sales_invoice,
            "exchange_applied_at": now_datetime(),
        },
        update_modified=True,
    )
    frappe.db.set_value(
        "Sales Invoice",
        sales_invoice,
        {
            "ch_exchange_order": exchange_order,
            "ch_exchange_credit": buyback_amount,
        },
        update_modified=True,
    )
    log_audit(
        "Exchange Applied to Invoice",
        "Buyback Exchange Order",
        exchange_order,
        new_value={
            "sales_invoice": sales_invoice,
            "buyback_amount": buyback_amount,
            "customer": exo.customer,
        },
    )

    return {
        "exchange_order": exchange_order,
        "sales_invoice": sales_invoice,
        "customer": exo.customer,
        "buyback_amount": buyback_amount,
        "amount_to_pay": flt(exo.amount_to_pay),
        "old_imei_serial": exo.old_imei_serial,
        "old_item_name": exo.old_item_name,
        "message": _(
            "Exchange credit ₹{0} applied to invoice {1}."
        ).format(buyback_amount, sales_invoice),
    }


@frappe.whitelist(methods=["POST"])
def raise_buyback_exception(
    order: str,
    requested_price: float,
    reason: str,
    exception_type: str = "Buyback Price Override",
) -> dict:
    """Store staff request a buyback **price override** on a Buyback Order.

    Mirrors the POS 'Discount Override' pattern: the proposed price and the
    order's current price are captured so the configured approver sees the change
    and the CH Exception framework can route by the requested amount. Tracked as
    a CH Exception Request linked back to this order.
    """
    if not reason or not reason.strip():
        frappe.throw(_("Please give a reason for the price change."),
                     title=_("Reason Required"), exc=BuybackValidationError)
    requested = flt(requested_price)
    if requested <= 0:
        frappe.throw(_("Requested buyback price must be greater than zero."),
                     title=_("Invalid Price"), exc=BuybackValidationError)

    if exception_type != "Buyback Price Override":
        frappe.throw(_("Unsupported Buyback exception type."), frappe.PermissionError)
    o = frappe.get_doc("Buyback Order", order)
    require_scoped_document_action(
        o, "order_operation_roles", _("raise a Buyback price exception")
    )

    # Current buyback price = manager-approved if present, else the computed
    # final price (base − deductions), else base price.
    current = flt(o.get("approved_price")) or flt(o.get("final_price")) or flt(o.get("base_price"))

    warehouse = (
        frappe.db.get_value("CH Store", o.store, "warehouse") if o.get("store") else None
    )
    # Buyback overrides go both ways — a store may need to *raise* the payout
    # (e.g. system 4,850 but customer wants 5,000), not only reduce it. Spell out
    # the direction so the manager sees it clearly even though the underlying
    # exception email is discount-oriented.
    delta = requested - current
    if delta > 0:
        direction = _("increase of ₹{0}").format(f"{delta:,.2f}")
    elif delta < 0:
        direction = _("decrease of ₹{0}").format(f"{abs(delta):,.2f}")
    else:
        direction = _("no change")
    full_reason = _("Buyback price override: ₹{0} → ₹{1} ({2}). {3}").format(
        f"{current:,.2f}", f"{requested:,.2f}", direction, reason.strip())

    from ch_item_master.ch_item_master.exception_api import raise_exception

    return raise_exception(
        exception_type=exception_type,
        company=o.company,
        reason=full_reason,
        requested_value=requested,   # proposed buyback payout → drives value-band routing
        original_value=current,      # current/system buyback price
        reference_doctype="Buyback Order",
        reference_name=o.name,
        item_code=o.get("item"),
        serial_no=o.get("serial_no") or o.get("imei_serial"),
        store_warehouse=warehouse,
        customer=o.get("customer"),
    )
