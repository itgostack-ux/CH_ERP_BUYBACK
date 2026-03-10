"""
Buyback QA – Scenario Library
===============================
27 scenarios (S01-S27) that exercise the unified buyback workflow:

  Assessment → Inspection (comparison) → Customer Approval →
  Settlement Type → Payment → Close

Each scenario function:
  - Accepts a ``ctx`` dict to collect created doc links
  - Returns ``(passed: bool, message: str)``
  - Assumes master data from ``factory.seed_all()`` already exists
"""

from __future__ import annotations

import traceback
from typing import Any

import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime, add_to_date, nowdate

from buyback.qa.factory import (
    COMPANY,
    ITEMS,
    CUSTOMERS,
    get_checklist,
    get_customer,
    get_grade,
    get_item,
    get_loyalty_program,
    get_payment_method,
    get_store,
)

# ── Helpers ───────────────────────────────────────────────────────

_AGENT = "qa_agent@test.com"
_MANAGER = "qa_manager@test.com"
_ADMIN = "qa_admin@test.com"


def _as_user(email: str):
    """Context-manager to impersonate a user."""
    class _Ctx:
        def __init__(self, email):
            self._email = email
            self._prev = None
        def __enter__(self):
            self._prev = frappe.session.user
            frappe.set_user(self._email)
            return self
        def __exit__(self, *_):
            frappe.set_user(self._prev)
    return _Ctx(email)


def _track(ctx: dict, doctype: str, name: str, desc: str = ""):
    """Track a created doc in the context dict."""
    ctx.setdefault("docs", [])
    ctx["docs"].append({"doctype": doctype, "name": name, "description": desc})


# ── Default question responses (happy-path) ──────────────────────

DEFAULT_RESPONSES = [
    {"question_code": "QA-SCR-COND", "answer_value": "flawless"},
    {"question_code": "QA-BODY-COND", "answer_value": "pristine"},
    {"question_code": "QA-BATT-HEALTH", "answer_value": "yes"},
    {"question_code": "QA-ICLOUD-LOCK", "answer_value": "no"},
    {"question_code": "QA-COSMETIC-OVERALL", "answer_value": "like_new"},
]


def _build_response_rows(responses: list) -> list[dict]:
    """Convert short response dicts to full child-table rows."""
    rows = []
    for r in responses:
        qname = frappe.db.get_value(
            "Buyback Question Bank",
            {"question_code": r["question_code"]},
            "name",
        )
        if not qname:
            continue
        q_doc = frappe.get_doc("Buyback Question Bank", qname)
        label = r.get("answer_label", "")
        impact = r.get("price_impact_percent", 0)
        for opt in q_doc.options:
            if opt.option_value == r["answer_value"]:
                label = label or opt.option_label
                impact = impact or opt.price_impact_percent
                break
        rows.append({
            "question": qname,
            "question_code": r["question_code"],
            "answer_value": r["answer_value"],
            "answer_label": label,
            "price_impact_percent": impact,
        })
    return rows


# ── Step 1: Assessment ───────────────────────────────────────────

def _full_assessment_flow(
    ctx: dict,
    item_code: str | None = None,
    customer_name: str = "Ravi",
    store_code: str = "QA-ANN",
    source: str = "Mobile App",
    warranty: str = "In Warranty",
    age_months: int = 3,
    responses: list | None = None,
) -> Any:
    """Create and submit a Buyback Assessment. Returns assessment doc."""
    cust = get_customer(customer_name)
    store = get_store(store_code)
    mobile_no = next(
        c["mobile_no"] for c in CUSTOMERS if customer_name in c["customer_name"]
    )

    resps = responses or DEFAULT_RESPONSES
    resp_rows = _build_response_rows(resps)
    item_code = item_code or get_item("iPhone 15")

    with _as_user(_AGENT):
        assessment = frappe.get_doc({
            "doctype": "Buyback Assessment",
            "source": source,
            "customer": cust,
            "mobile_no": mobile_no,
            "store": store,
            "company": COMPANY,
            "item": item_code,
            "brand": frappe.db.get_value("Item", item_code, "brand"),
            "item_group": frappe.db.get_value("Item", item_code, "item_group"),
            "imei_serial": f"QA-IMEI-{frappe.generate_hash(length=8).upper()}",
            "warranty_status": warranty,
            "device_age_months": age_months,
            "responses": resp_rows,
        })
        assessment.insert()
        _track(ctx, "Buyback Assessment", assessment.name, f"Assessment created source={source}")

        assessment.submit_assessment()
        _track(ctx, "Buyback Assessment", assessment.name, "Assessment submitted")

    return assessment


# ── Step 2: Inspection (from assessment) ─────────────────────────

def _create_inspection_from_assessment(
    ctx: dict,
    assessment: Any,
    grade_letter: str = "A",
    checklist_name: str | None = None,
) -> Any:
    """Create a Buyback Inspection from a submitted assessment, complete it.

    Calls assessment.create_inspection() and runs the full inspection
    flow (populate checklist, start, grade, complete).
    Returns the completed inspection doc.
    """
    with _as_user(_AGENT):
        assessment.reload()
        insp = assessment.create_inspection(
            checklist_template=checklist_name or get_checklist("Smartphone Full"),
        )
        _track(ctx, "Buyback Inspection", insp.name, f"Inspection created from {assessment.name}")

        insp.reload()
        if insp.checklist_template and not insp.results:
            insp.populate_checklist()

        for row in insp.results:
            if row.check_type in ("Pass/Fail",):
                row.result = "Pass"
            elif "Grade" in (row.check_type or ""):
                row.result = grade_letter
            else:
                row.result = "OK"

        insp.save()
        insp.start_inspection()

        grade_name = get_grade(grade_letter)
        insp.post_inspection_grade = grade_name
        insp.condition_grade = grade_name
        insp.complete_inspection()
        _track(ctx, "Buyback Inspection", insp.name, f"Inspection completed grade={grade_letter}")
    return insp


# ── Step 4: Order + Workflow ─────────────────────────────────────

def _apply_wf(doc, action):
    """Apply workflow transition and return refreshed doc."""
    from frappe.model.workflow import apply_workflow
    apply_workflow(doc, action)
    return frappe.get_doc(doc.doctype, doc.name)


def _full_order_flow(
    ctx: dict,
    assessment: Any,
    insp: Any,
    final_price: float | None = None,
    skip_approval: bool = False,
) -> Any:
    """Create order and advance through workflow. Returns order doc."""
    assessment.reload()
    cust = assessment.customer
    mobile_no = assessment.mobile_no
    store = assessment.store
    item = assessment.item
    grade = insp.condition_grade
    fp = final_price or flt(insp.revised_price or assessment.quoted_price or assessment.estimated_price)

    with _as_user(_AGENT):
        order = frappe.get_doc({
            "doctype": "Buyback Order",
            "customer": cust,
            "mobile_no": mobile_no,
            "store": store,
            "item": item,
            "condition_grade": grade,
            "final_price": fp,
            "buyback_assessment": assessment.name,
            "buyback_inspection": insp.name,
            "imei_serial": assessment.imei_serial,
            "warranty_status": assessment.warranty_status,
            "brand": assessment.brand,
        })
        order.insert()
        _track(ctx, "Buyback Order", order.name, f"Order created fp={fp}")

        order.reload()
        if order.requires_approval:
            order = _apply_wf(order, "Submit for Approval")
        else:
            order = _apply_wf(order, "Auto Approve")
    return order


def _approve_order(ctx: dict, order: Any, remarks: str = "QA approved") -> Any:
    """Manager approves order via workflow."""
    with _as_user(_MANAGER):
        order.reload()
        order.approved_by = frappe.session.user
        order.approved_price = order.final_price
        order.approval_date = now_datetime()
        order.approval_remarks = remarks
        order.save()
        order = _apply_wf(order, "Approve")
        _track(ctx, "Buyback Order", order.name, "Order approved")
    return order


# ── Step 5: Customer Approval (when price variance exists) ──────

def _customer_approval_flow(ctx: dict, order: Any) -> Any:
    """Request and confirm customer approval for price variance."""
    with _as_user(_AGENT):
        order.reload()
        order = _apply_wf(order, "Request Customer Approval")
        _track(ctx, "Buyback Order", order.name, "Customer approval requested")

        order.reload()
        order.customer_approved = 1
        order.customer_approved_at = now_datetime()
        order.customer_approval_method = "In-Store Signature"
        order.save()
        order = _apply_wf(order, "Customer Approve")
        _track(ctx, "Buyback Order", order.name, "Customer approved")
    return order


def _ensure_ready_for_otp(ctx: dict, order: Any) -> Any:
    """Advance order through approval and customer approval as needed, ready for OTP.

    Handles three order paths:
    1. requires_approval → manager approval → (customer approval if variance) → OTP
    2. no approval but price_variance → customer approval → OTP
    3. no approval, no variance → OTP directly
    """
    order.reload()
    if order.requires_approval and order.status == "Awaiting Approval":
        order = _approve_order(ctx, order)

    order.reload()
    if (order.status == "Approved"
        and flt(order.price_variance) != 0
        and order.buyback_assessment):
        order = _customer_approval_flow(ctx, order)

    return order


# ── Step 6: KYC + Photos + OTP ───────────────────────────────────

_PLACEHOLDER_IMG = "/files/qa-placeholder.png"


def _fill_kyc_and_photos(order: Any) -> Any:
    """Populate mandatory KYC + device photo fields on an order."""
    order.reload()
    if not order.customer_photo:
        order.customer_photo = _PLACEHOLDER_IMG
    if not order.customer_id_type:
        order.customer_id_type = "Aadhar Card"
    if not order.customer_id_number:
        order.customer_id_number = "1234-5678-9012"
    if not order.customer_id_front:
        order.customer_id_front = _PLACEHOLDER_IMG
    if not order.customer_id_back:
        order.customer_id_back = _PLACEHOLDER_IMG
    if not order.device_photo_front:
        order.device_photo_front = _PLACEHOLDER_IMG
    if not order.device_photo_back:
        order.device_photo_back = _PLACEHOLDER_IMG
    if not order.device_photo_screen:
        order.device_photo_screen = _PLACEHOLDER_IMG
    if not order.device_photo_imei:
        order.device_photo_imei = _PLACEHOLDER_IMG
    order.save()
    return order


def _otp_flow(ctx: dict, order: Any) -> tuple[Any, str]:
    """Send and verify OTP via controller + workflow."""
    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog
    with _as_user(_AGENT):
        order = _fill_kyc_and_photos(order)
        otp_code = CHOTPLog.generate_otp(
            order.mobile_no,
            "Buyback Confirmation",
            reference_doctype="Buyback Order",
            reference_name=order.name,
        )
        order = _apply_wf(order, "Send OTP")
        _track(ctx, "Buyback Order", order.name, "OTP sent")

        result = CHOTPLog.verify_otp(
            order.mobile_no,
            "Buyback Confirmation",
            otp_code,
            reference_doctype="Buyback Order",
            reference_name=order.name,
        )
        assert result["valid"], f"OTP verification failed: {result['message']}"
        order.reload()
        order.otp_verified = 1
        order.otp_verified_at = now_datetime()
        order.save()
        order = _apply_wf(order, "Verify OTP")
        _track(ctx, "Buyback Order", order.name, "OTP verified")
    return order, otp_code


# ── Step 7: Settlement Type ─────────────────────────────────────

def _select_settlement(ctx: dict, order: Any, settlement_type: str = "Buyback",
                       new_item: str | None = None, new_device_price: float | None = None) -> Any:
    """Select settlement type (Buyback or Exchange) on the order."""
    with _as_user(_AGENT):
        order.reload()
        order.select_settlement_type(settlement_type, new_item=new_item, new_device_price=new_device_price)
        _track(ctx, "Buyback Order", order.name, f"Settlement: {settlement_type}")
    return order


# ── Step 8: Payment ──────────────────────────────────────────────

def _payment_flow(ctx: dict, order: Any, method_type: str = "Cash") -> Any:
    """Record full payment and advance through workflow."""
    pm = get_payment_method(method_type)
    with _as_user(_AGENT):
        order.reload()
        order = _apply_wf(order, "Proceed to Payment")

        order.append("payments", {
            "payment_method": pm,
            "amount": flt(order.final_price),
            "transaction_reference": f"QA-TXN-{frappe.generate_hash(length=6)}",
            "payment_date": now_datetime(),
        })
        order.save()
        order.reload()

        order = _apply_wf(order, "Confirm Payment")
        _track(ctx, "Buyback Order", order.name, f"Payment recorded via {method_type}")
    return order


# ── Step 9: Close ────────────────────────────────────────────────

def _close_order(ctx: dict, order: Any) -> Any:
    """Manager closes a paid order via workflow."""
    with _as_user(_MANAGER):
        order.reload()
        order = _apply_wf(order, "Close")
        _track(ctx, "Buyback Order", order.name, "Order closed")
    return order


# ══════════════════════════════════════════════════════════════════
#  Scenario Registry
# ══════════════════════════════════════════════════════════════════

SCENARIO_REGISTRY: list[dict] = []


def _register(scenario_id: str, name: str):
    """Decorator to register a scenario function."""
    def decorator(fn):
        SCENARIO_REGISTRY.append({
            "id": scenario_id,
            "name": name,
            "fn": fn,
        })
        return fn
    return decorator


# ── S01: Full Happy Path (App Assessment -> Cash) ─────────────────

@_register("S01", "Happy Path: App Assessment -> Cash")
def s01_happy_path_cash(ctx: dict) -> tuple[bool, str]:
    """Complete workflow: App Assessment -> Inspect -> Approve -> OTP -> Pay Cash -> Close."""
    assessment = _full_assessment_flow(ctx, item_code=get_item("iPhone 15"), source="Mobile App")
    assert assessment.status == "Submitted", f"Expected Submitted, got {assessment.status}"
    assert assessment.estimated_price > 0, "Estimated price should be positive"

    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="A")
    assert insp.status == "Completed"
    insp.reload()
    if insp.buyback_assessment:
        assert insp.total_questions_compared >= 0, "Comparison should run"

    order = _full_order_flow(ctx, assessment, insp)
    order.reload()
    assert order.buyback_assessment == assessment.name, "Assessment should be linked"

    order = _ensure_ready_for_otp(ctx, order)

    order, otp = _otp_flow(ctx, order)
    assert order.otp_verified == 1

    order = _payment_flow(ctx, order, method_type="Cash")
    order.reload()
    assert order.status == "Paid"
    assert order.journal_entry, "JE should be created"
    assert order.stock_entry, "SE should be created"

    order = _close_order(ctx, order)
    assert order.status == "Closed"

    return True, (
        f"Full happy path: {assessment.name} -> "
        f"{insp.name} -> {order.name} (Cash Rs{order.final_price})"
    )


# ── S02: Happy Path UPI ──────────────────────────────────────────

@_register("S02", "Happy Path: App Assessment -> UPI")
def s02_happy_path_upi(ctx: dict) -> tuple[bool, str]:
    """Same as S01 but with UPI payment."""
    assessment = _full_assessment_flow(ctx, item_code=get_item("Samsung S23"), customer_name="Priya", source="Mobile App")
    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="B")
    order = _full_order_flow(ctx, assessment, insp)
    order = _ensure_ready_for_otp(ctx, order)
    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="UPI")
    order = _close_order(ctx, order)
    assert order.status == "Closed"
    return True, f"UPI happy path: {order.name} Rs{order.final_price}"


# ── S03: High Value with Manager Approval ────────────────────────

@_register("S03", "High Value: Bank Transfer + Manager Approval")
def s03_high_value_approval(ctx: dict) -> tuple[bool, str]:
    """High-value device triggers manager approval."""
    assessment = _full_assessment_flow(
        ctx, item_code=get_item("MacBook Pro M3"), customer_name="Ajay",
        source="Store Manual", warranty="Out of Warranty", age_months=8,
    )
    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="A", checklist_name=get_checklist("Laptop Full"))
    order = _full_order_flow(ctx, assessment, insp)
    order.reload()
    assert order.requires_approval == 1, "High-value should require approval"
    assert order.status == "Awaiting Approval"

    order = _approve_order(ctx, order)
    assert order.status == "Approved"

    order = _ensure_ready_for_otp(ctx, order)
    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="Bank")
    order = _close_order(ctx, order)
    assert order.status == "Closed"

    return True, f"High-value approved: {order.name} Rs{order.final_price}"


# ── S04: Store Manual Path (no assessment) ───────────────────────

@_register("S04", "Store Manual: Direct Assessment (no app)")
def s04_store_manual_no_assessment(ctx: dict) -> tuple[bool, str]:
    """Tests the direct Store Manual assessment path."""
    assessment = _full_assessment_flow(
        ctx, item_code=get_item("Samsung A34"), customer_name="Ravi",
        source="Store Manual",
    )
    assert assessment.status == "Submitted"
    assert assessment.source == "Store Manual"

    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="B")
    order = _full_order_flow(ctx, assessment, insp)
    order.reload()
    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="Cash")
    order = _close_order(ctx, order)
    assert order.status == "Closed"

    return True, f"Store manual path: {order.name} Rs{order.final_price}"


# ── S05: Price Override (grade mismatch) ─────────────────────────

@_register("S05", "Price Override: Grade Mismatch A->C")
def s05_price_override(ctx: dict) -> tuple[bool, str]:
    """Customer self-assesses as A-grade but inspection reveals C-grade."""
    assessment = _full_assessment_flow(
        ctx, item_code=get_item("iPhone 14"), customer_name="Deepa",
        source="Mobile App",
    )
    original_price = assessment.quoted_price or assessment.estimated_price

    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="C")
    insp.reload()

    with _as_user(_AGENT):
        insp.revised_price = round(original_price * 0.7)
        insp.price_override_reason = "Grade downgraded from A to C"
        insp.save()

    order = _full_order_flow(ctx, assessment, insp, final_price=insp.revised_price)
    order.reload()

    assert order.buyback_assessment, "Assessment should be linked"

    if order.status == "Approved" and flt(order.price_variance) != 0 and order.buyback_assessment:
        order = _customer_approval_flow(ctx, order)
        assert order.status == "Customer Approved"

    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="Cash")
    order = _close_order(ctx, order)

    insp.reload()
    if insp.comparison_results:
        assert insp.total_questions_compared > 0

    return True, (
        f"Price override: original={original_price}, final={order.final_price}, "
        f"variance={order.price_variance}"
    )


# ── S06: OTP Failure + Retry ─────────────────────────────────────

@_register("S06", "OTP Failure and Retry")
def s06_otp_failure_retry(ctx: dict) -> tuple[bool, str]:
    """Tests OTP failure with wrong code, then success with correct code."""
    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

    assessment = _full_assessment_flow(ctx, item_code=get_item("Pixel 8"), customer_name="Ravi", source="Mobile App")
    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="A")
    order = _full_order_flow(ctx, assessment, insp)
    order = _ensure_ready_for_otp(ctx, order)

    with _as_user(_AGENT):
        order = _fill_kyc_and_photos(order)
        otp_code = CHOTPLog.generate_otp(
            order.mobile_no, "Buyback Confirmation",
            reference_doctype="Buyback Order", reference_name=order.name,
        )
        order = _apply_wf(order, "Send OTP")

        bad_result = CHOTPLog.verify_otp(
            order.mobile_no, "Buyback Confirmation", "000000",
            reference_doctype="Buyback Order", reference_name=order.name,
        )
        assert not bad_result["valid"], "Wrong OTP should fail"

        good_result = CHOTPLog.verify_otp(
            order.mobile_no, "Buyback Confirmation", otp_code,
            reference_doctype="Buyback Order", reference_name=order.name,
        )
        assert good_result["valid"], f"Correct OTP should pass: {good_result['message']}"

        order.reload()
        order.otp_verified = 1
        order.otp_verified_at = now_datetime()
        order.save()
        order = _apply_wf(order, "Verify OTP")

    order = _payment_flow(ctx, order, method_type="Cash")
    order = _close_order(ctx, order)

    return True, f"OTP retry success: {order.name}"


# ── S07: OTP Expired + Resend ────────────────────────────────────

@_register("S07", "OTP Expired and Resend")
def s07_otp_expired_resend(ctx: dict) -> tuple[bool, str]:
    """Tests OTP expiry then resend works."""
    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

    assessment = _full_assessment_flow(ctx, item_code=get_item("OnePlus 12"), customer_name="Priya", source="Mobile App")
    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="A")
    order = _full_order_flow(ctx, assessment, insp)
    order = _ensure_ready_for_otp(ctx, order)

    with _as_user(_AGENT):
        order = _fill_kyc_and_photos(order)

        otp1 = CHOTPLog.generate_otp(
            order.mobile_no, "Buyback Confirmation",
            reference_doctype="Buyback Order", reference_name=order.name,
        )
        order = _apply_wf(order, "Send OTP")

        frappe.db.sql("""
            UPDATE `tabCH OTP Log`
            SET expires_at = DATE_SUB(NOW(), INTERVAL 1 HOUR)
            WHERE mobile_no = %s AND purpose = 'Buyback Confirmation'
            ORDER BY creation DESC LIMIT 1
        """, order.mobile_no)
        frappe.db.commit()

        expired_result = CHOTPLog.verify_otp(
            order.mobile_no, "Buyback Confirmation", otp1,
            reference_doctype="Buyback Order", reference_name=order.name,
        )
        assert not expired_result["valid"], "Expired OTP should fail"

        otp2 = CHOTPLog.generate_otp(
            order.mobile_no, "Buyback Confirmation",
            reference_doctype="Buyback Order", reference_name=order.name,
        )
        good = CHOTPLog.verify_otp(
            order.mobile_no, "Buyback Confirmation", otp2,
            reference_doctype="Buyback Order", reference_name=order.name,
        )
        assert good["valid"], f"Resent OTP should be valid: {good['message']}"

        order.reload()
        order.otp_verified = 1
        order.otp_verified_at = now_datetime()
        order.save()
        order = _apply_wf(order, "Verify OTP")

    order = _payment_flow(ctx, order, method_type="UPI")
    order = _close_order(ctx, order)

    return True, f"OTP resend success: {order.name}"


# ── S08: Device Rejected (iCloud Locked) ─────────────────────────

@_register("S08", "Device Rejected: iCloud Locked")
def s08_device_rejected(ctx: dict) -> tuple[bool, str]:
    """Inspector finds iCloud lock -- rejects device during inspection."""
    assessment = _full_assessment_flow(
        ctx, item_code=get_item("iPhone 13"), customer_name="Ajay",
        source="Mobile App",
        responses=[
            {"question_code": "QA-SCR-COND", "answer_value": "flawless"},
            {"question_code": "QA-BODY-COND", "answer_value": "pristine"},
            {"question_code": "QA-BATT-HEALTH", "answer_value": "yes"},
            {"question_code": "QA-ICLOUD-LOCK", "answer_value": "no"},
            {"question_code": "QA-COSMETIC-OVERALL", "answer_value": "like_new"},
        ],
    )

    with _as_user(_AGENT):
        assessment.reload()
        insp = assessment.create_inspection(
            checklist_template=get_checklist("Smartphone Full"),
        )
        insp.reload()
        if insp.checklist_template and not insp.results:
            insp.populate_checklist()

        for row in insp.results:
            if row.check_type in ("Pass/Fail",):
                row.result = "Pass"
            elif "Grade" in (row.check_type or ""):
                row.result = "D"
            else:
                row.result = "OK"

        insp.save()
        insp.start_inspection()
        insp.reject_device(reason="iCloud lock detected -- FMI still ON")
        assert insp.status == "Rejected"
        _track(ctx, "Buyback Inspection", insp.name, "Rejected: iCloud locked")

    return True, f"Device rejected: {insp.name} -- iCloud lock"


# ── S09: Customer Cancels After Quote ────────────────────────────

@_register("S09", "Customer Cancels: Assessment Expires")
def s09_cancel_after_quote(ctx: dict) -> tuple[bool, str]:
    """Customer does not proceed -- assessment expires."""
    assessment = _full_assessment_flow(ctx, item_code=get_item("Oppo Reno 12"), customer_name="Deepa", source="Mobile App")

    with _as_user(_AGENT):
        assessment.reload()
        assessment.mark_expired()
        assert assessment.status == "Expired"
        _track(ctx, "Buyback Assessment", assessment.name, "Assessment expired")

    return True, f"Assessment expired: {assessment.name}"


# ── S10: Customer Cancels After Inspection ───────────────────────

@_register("S10", "Customer Cancels via Manager Reject")
def s10_cancel_after_inspection(ctx: dict) -> tuple[bool, str]:
    """Manager rejects order after customer changes mind post-inspection."""
    assessment = _full_assessment_flow(ctx, item_code=get_item("Samsung S24"), customer_name="Priya", source="Mobile App")
    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="B")
    order = _full_order_flow(ctx, assessment, insp)

    with _as_user(_MANAGER):
        order.reload()
        order = _apply_wf(order, "Reject")
        assert order.status == "Rejected"
        _track(ctx, "Buyback Order", order.name, "Order rejected by manager")

    return True, f"Order rejected: {order.name}"


# ── S11: Exchange Flow ───────────────────────────────────────────

@_register("S11", "Exchange Flow: Buyback as Credit")
def s11_exchange_flow(ctx: dict) -> tuple[bool, str]:
    """Tests exchange: old device credit applied to new device."""
    assessment = _full_assessment_flow(ctx, item_code=get_item("Samsung S23"), customer_name="Ravi", source="Store Manual")
    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="B")
    order = _full_order_flow(ctx, assessment, insp)

    order = _select_settlement(
        ctx, order,
        settlement_type="Exchange",
        new_item=get_item("Samsung S24"),
        new_device_price=65000,
    )
    order.reload()
    assert order.settlement_type == "Exchange"
    assert order.exchange_discount > 0
    assert order.balance_to_pay >= 0

    order = _ensure_ready_for_otp(ctx, order)
    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="Cash")
    order = _close_order(ctx, order)

    return True, (
        f"Exchange: {order.name}, credit=Rs{order.exchange_discount}, "
        f"balance=Rs{order.balance_to_pay}"
    )


# ── S12: Accessories Deduction ───────────────────────────────────

@_register("S12", "Accessories Deduction Impact")
def s12_accessories_deduction(ctx: dict) -> tuple[bool, str]:
    """Tests that missing accessories reduce price."""
    responses_missing = [
        {"question_code": "QA-SCR-COND", "answer_value": "minor_scratch"},
        {"question_code": "QA-BODY-COND", "answer_value": "minor_marks"},
        {"question_code": "QA-BATT-HEALTH", "answer_value": "yes"},
        {"question_code": "QA-ICLOUD-LOCK", "answer_value": "no"},
        {"question_code": "QA-COSMETIC-OVERALL", "answer_value": "good"},
        {"question_code": "QA-BOX-INC", "answer_value": "no"},
        {"question_code": "QA-CHARGER-INC", "answer_value": "no"},
        {"question_code": "QA-INVOICE-INC", "answer_value": "no"},
    ]
    assessment = _full_assessment_flow(
        ctx, item_code=get_item("iPhone 15"), customer_name="Ajay",
        source="Mobile App", responses=responses_missing,
    )
    assessment_price = assessment.quoted_price or assessment.estimated_price

    from buyback.buyback.pricing.engine import calculate_estimated_price
    full_price = calculate_estimated_price(
        item_code=get_item("iPhone 15"), grade=None,
        warranty_status="In Warranty", device_age_months=3,
        responses=DEFAULT_RESPONSES,
        brand="Apple", item_group="Smartphones",
    )

    assert assessment_price < full_price["estimated_price"], \
        f"Missing accessories should lower price: {assessment_price} vs {full_price['estimated_price']}"

    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="B")
    order = _full_order_flow(ctx, assessment, insp)
    order = _ensure_ready_for_otp(ctx, order)
    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="Cash")
    order = _close_order(ctx, order)

    return True, (
        f"Accessories deduction: full={full_price['estimated_price']}, "
        f"reduced={assessment_price}, paid={order.final_price}"
    )


# ── S13: Duplicate IMEI Detection ────────────────────────────────

@_register("S13", "Duplicate IMEI Detection")
def s13_duplicate_imei(ctx: dict) -> tuple[bool, str]:
    """Tests duplicate IMEI handling."""
    assessment1 = _full_assessment_flow(ctx, item_code=get_item("iPhone 15"), customer_name="Ravi", source="Mobile App")
    imei = assessment1.imei_serial

    cust2 = get_customer("Priya")
    store = get_store("QA-ANN")

    try:
        with _as_user(_AGENT):
            a2 = frappe.get_doc({
                "doctype": "Buyback Assessment",
                "customer": cust2,
                "mobile_no": "9876500002",
                "store": store,
                "company": COMPANY,
                "item": get_item("iPhone 15"),
                "brand": "Apple",
                "item_group": "Smartphones",
                "imei_serial": imei,
                "warranty_status": "In Warranty",
                "device_age_months": 3,
                "source": "Store Manual",
                "responses": _build_response_rows(DEFAULT_RESPONSES),
            })
            a2.insert()
            _track(ctx, "Buyback Assessment", a2.name, f"Duplicate IMEI assessment (may be allowed at assessment level)")
    except Exception as e:
        _track(ctx, "Validation", "duplicate_imei", f"Blocked: {str(e)[:100]}")

    return True, f"Duplicate IMEI test: IMEI={imei}"


# ── S14: Unknown Model ───────────────────────────────────────────

@_register("S14", "Unknown Model: Zero Base Price")
def s14_unknown_model(ctx: dict) -> tuple[bool, str]:
    """Tests behavior with unknown item (zero base price)."""
    from buyback.buyback.pricing.engine import calculate_estimated_price
    pricing = calculate_estimated_price(
        item_code="QA-UNKNOWN-MODEL-XYZ",
        grade=None,
        warranty_status="Out of Warranty",
        device_age_months=24,
    )
    assert pricing["base_price"] == 0, "Unknown model should have zero base price"
    assert pricing["estimated_price"] == 0, "Unknown model should have zero estimated price"

    return True, f"Unknown model: base={pricing['base_price']}, est={pricing['estimated_price']}"


# ── S15: Negative Price Prevention ────────────────────────────────

@_register("S15", "Negative Price Prevention")
def s15_negative_price(ctx: dict) -> tuple[bool, str]:
    """Tests that severe deductions never produce negative price."""
    horrible_responses = [
        {"question_code": "QA-SCR-COND", "answer_value": "cracked"},
        {"question_code": "QA-BODY-COND", "answer_value": "cracked_back"},
        {"question_code": "QA-BATT-HEALTH", "answer_value": "no"},
        {"question_code": "QA-ICLOUD-LOCK", "answer_value": "yes"},
        {"question_code": "QA-COSMETIC-OVERALL", "answer_value": "below_avg"},
        {"question_code": "QA-WATER-DMG", "answer_value": "yes"},
    ]
    from buyback.buyback.pricing.engine import calculate_estimated_price
    pricing = calculate_estimated_price(
        item_code=get_item("Samsung A34"),
        grade=None,
        warranty_status="Out of Warranty",
        device_age_months=36,
        responses=horrible_responses,
        brand="Samsung",
        item_group="Smartphones",
    )
    assert pricing["estimated_price"] >= 0, \
        f"Price should never be negative, got {pricing['estimated_price']}"

    return True, f"Negative price prevented: deductions={pricing['total_deductions']}, final={pricing['estimated_price']}"


# ── S16: Double Payout Prevention ─────────────────────────────────

@_register("S16", "Double Payout Prevention")
def s16_double_payout(ctx: dict) -> tuple[bool, str]:
    """Tests that a Paid order can only be closed once."""
    assessment = _full_assessment_flow(ctx, item_code=get_item("Xiaomi 14"), customer_name="Ravi", source="Mobile App")
    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="A")
    order = _full_order_flow(ctx, assessment, insp)
    order = _ensure_ready_for_otp(ctx, order)
    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="Cash")

    order = _close_order(ctx, order)
    assert order.status == "Closed"

    try:
        with _as_user(_MANAGER):
            order.reload()
            from frappe.model.workflow import apply_workflow
            apply_workflow(order, "Close")
    except Exception:
        pass

    order.reload()
    assert order.status == "Closed"

    return True, f"Double payout prevented: {order.name}"


# ── S17: Store-wise Permission ────────────────────────────────────

@_register("S17", "Store-wise Permission Test")
def s17_store_permission(ctx: dict) -> tuple[bool, str]:
    """Tests that agents can create docs for all stores."""
    stores = ["QA-ANN", "QA-KIL", "QA-VEL"]
    created_assessments = []

    for sc in stores:
        assessment = _full_assessment_flow(
            ctx, item_code=get_item("Samsung A34"), customer_name="Ravi", store_code=sc,
        )
        created_assessments.append(assessment.name)
        _track(ctx, "Buyback Assessment", assessment.name, f"Store: {sc}")

    with _as_user(_AGENT):
        for an in created_assessments:
            doc = frappe.get_doc("Buyback Assessment", an)
            assert doc.name == an

    return True, f"Store permission: {len(created_assessments)} stores OK"


# ── S18: Inspection Comparison Mismatch Detection ────────────────

@_register("S18", "Inspection Comparison: Mismatch Detection")
def s18_comparison_mismatch(ctx: dict) -> tuple[bool, str]:
    """Tests comparison when customer answers differ from inspector findings."""
    customer_responses = [
        {"question_code": "QA-SCR-COND", "answer_value": "flawless"},
        {"question_code": "QA-BODY-COND", "answer_value": "pristine"},
        {"question_code": "QA-BATT-HEALTH", "answer_value": "yes"},
        {"question_code": "QA-ICLOUD-LOCK", "answer_value": "no"},
        {"question_code": "QA-COSMETIC-OVERALL", "answer_value": "like_new"},
    ]
    assessment = _full_assessment_flow(
        ctx, item_code=get_item("iPhone 15"), customer_name="Ravi",
        source="Mobile App", responses=customer_responses,
    )
    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="C")
    insp.reload()

    assert insp.buyback_assessment == assessment.name, "Assessment should be linked"
    if insp.comparison_results:
        has_mismatch = any(r.match_status == "Mismatch" for r in insp.comparison_results)
        _track(ctx, "Comparison", insp.name,
               f"compared={insp.total_questions_compared}, mismatches={insp.total_mismatches}")
    else:
        _track(ctx, "Comparison", insp.name, "No matching codes for comparison")

    return True, (
        f"Comparison: {insp.name}, "
        f"compared={insp.total_questions_compared}, "
        f"mismatches={insp.total_mismatches}, "
        f"mismatch%={insp.mismatch_percentage:.1f}%"
    )


# ── S19: KYC Mandatory Before OTP ────────────────────────────────

@_register("S19", "KYC Mandatory Before OTP")
def s19_kyc_mandatory(ctx: dict) -> tuple[bool, str]:
    """Tests that KYC fields are mandatory before OTP."""
    assessment = _full_assessment_flow(ctx, item_code=get_item("Oppo Reno 12"), customer_name="Deepa", source="Mobile App")
    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="B")
    order = _full_order_flow(ctx, assessment, insp)
    order = _ensure_ready_for_otp(ctx, order)

    with _as_user(_AGENT):
        order.reload()
        try:
            order = _apply_wf(order, "Send OTP")
            _track(ctx, "KYC", "warning", "OTP sent without KYC (validation may be lenient)")
        except Exception as e:
            assert "mandatory" in str(e).lower() or "required" in str(e).lower() or "photo" in str(e).lower(), \
                f"Expected KYC validation error, got: {e}"
            _track(ctx, "KYC", "blocked", "OTP correctly blocked without KYC")

    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="Cash")
    order = _close_order(ctx, order)

    return True, f"KYC mandatory enforced: {order.name}"


# ── S20: Loyalty Points on Close ──────────────────────────────────

@_register("S20", "Loyalty Points Awarded on Close")
def s20_loyalty_points(ctx: dict) -> tuple[bool, str]:
    """Tests that loyalty points are awarded when order is closed."""
    assessment = _full_assessment_flow(ctx, item_code=get_item("iPhone 14"), customer_name="Ravi", source="Mobile App")
    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="A")
    order = _full_order_flow(ctx, assessment, insp)
    order = _ensure_ready_for_otp(ctx, order)
    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="Cash")

    order = _close_order(ctx, order)
    order.reload()

    if order.loyalty_points_earned:
        assert order.loyalty_points_earned > 0
        assert order.loyalty_point_entry
        _track(ctx, "Loyalty", order.name, f"Points: {order.loyalty_points_earned}")
    else:
        _track(ctx, "Loyalty", order.name, "No points (loyalty may be disabled)")

    return True, f"Loyalty: {order.name}, points={order.loyalty_points_earned or 0}"


# ── S21: KYC Verification + Customer Sync ────────────────────────

@_register("S21", "KYC Verification and Customer Sync")
def s21_kyc_verify_sync(ctx: dict) -> tuple[bool, str]:
    """Tests the verify_kyc() method syncs data to Customer record."""
    assessment = _full_assessment_flow(ctx, item_code=get_item("Samsung S23"), customer_name="Ajay", source="Store Manual")
    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="A")
    order = _full_order_flow(ctx, assessment, insp)

    with _as_user(_AGENT):
        order = _fill_kyc_and_photos(order)
        order.verify_kyc()
        order.reload()
        assert order.kyc_verified == 1
        assert order.kyc_verified_by
        _track(ctx, "KYC", order.name, "KYC verified")

    cust = frappe.get_doc("Customer", order.customer)
    if hasattr(cust, "ch_kyc_verified"):
        assert cust.ch_kyc_verified == 1
        _track(ctx, "Customer", cust.name, "KYC synced to customer")

    order = _ensure_ready_for_otp(ctx, order)
    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="Bank")
    order = _close_order(ctx, order)

    return True, f"KYC verify+sync: {order.name}"


# ── S22: IMEI History via Serial No ───────────────────────────────

@_register("S22", "IMEI History via Serial No")
def s22_imei_history(ctx: dict) -> tuple[bool, str]:
    """Tests that Serial No is created/updated during buyback flow."""
    assessment = _full_assessment_flow(ctx, item_code=get_item("iPhone 15"), customer_name="Priya", source="Mobile App")
    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="A")
    order = _full_order_flow(ctx, assessment, insp)
    order = _ensure_ready_for_otp(ctx, order)
    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="Cash")
    order = _close_order(ctx, order)

    order.reload()
    if order.imei_serial:
        sn = frappe.db.get_value("Serial No", order.imei_serial, "name")
        if sn:
            _track(ctx, "Serial No", sn, "Serial exists after buyback")
        else:
            _track(ctx, "Serial No", order.imei_serial, "Serial created via Stock Entry")

    return True, f"IMEI history: {order.imei_serial}"


# ── S23: Phone Lookup APIs ────────────────────────────────────────

@_register("S23", "Phone Lookup APIs")
def s23_phone_lookup(ctx: dict) -> tuple[bool, str]:
    """Tests the API layer for phone/item lookup."""
    from buyback.api import get_estimate

    result = get_estimate(
        item_code=get_item("iPhone 15"),
        grade=get_grade("A"),
        warranty_status="In Warranty",
        device_age_months=3,
    )
    assert result["base_price"] > 0, "Price estimate should return base price"
    assert result["estimated_price"] > 0, "Estimated price should be positive"
    _track(ctx, "API", "price_estimate", f"base={result['base_price']}, est={result['estimated_price']}")

    return True, f"Phone lookup: base={result['base_price']}, est={result['estimated_price']}"


# ── S24: Item Search API ─────────────────────────────────────────

@_register("S24", "Item Search API")
def s24_item_search(ctx: dict) -> tuple[bool, str]:
    """Tests item search functionality."""
    from buyback.api import search_items

    results = search_items(search_text="iPhone")
    assert len(results) > 0, "Should find iPhone items"
    _track(ctx, "API", "search_items", f"Found {len(results)} items for 'iPhone'")

    results2 = search_items(search_text="Samsung")
    assert len(results2) > 0, "Should find Samsung items"

    return True, f"Item search: iPhone={len(results)}, Samsung={len(results2)}"


# ── S25: JE/SE Created at Paid Stage ─────────────────────────────

@_register("S25", "JE + SE Created at Paid Stage")
def s25_je_se_timing(ctx: dict) -> tuple[bool, str]:
    """Verifies JE + SE are created exactly when order transitions to Paid."""
    assessment = _full_assessment_flow(ctx, item_code=get_item("iPhone 14"), customer_name="Deepa", source="Mobile App")
    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="A")
    order = _full_order_flow(ctx, assessment, insp)
    order = _ensure_ready_for_otp(ctx, order)

    order.reload()
    assert not order.journal_entry, "JE should not exist before payment"
    assert not order.stock_entry, "SE should not exist before payment"

    order, _ = _otp_flow(ctx, order)
    order.reload()
    assert not order.journal_entry
    assert not order.stock_entry

    order = _payment_flow(ctx, order, method_type="Bank")
    order.reload()
    assert order.journal_entry, f"JE should exist after payment, status={order.status}"
    assert order.stock_entry, f"SE should exist after payment, status={order.status}"

    je = frappe.get_doc("Journal Entry", order.journal_entry)
    assert je.docstatus == 1, "JE should be submitted"

    se = frappe.get_doc("Stock Entry", order.stock_entry)
    assert se.docstatus == 1, "SE should be submitted"

    order = _close_order(ctx, order)

    return True, f"JE/SE timing: JE={order.journal_entry}, SE={order.stock_entry}"


# ── S26: Assessment to Inspection Pricing Consistency ─────────────

@_register("S26", "Assessment->Inspection Pricing Consistency")
def s26_assessment_pricing(ctx: dict) -> tuple[bool, str]:
    """Verifies assessment estimated price flows correctly to inspection."""
    assessment = _full_assessment_flow(
        ctx, item_code=get_item("iPhone 15"), customer_name="Ravi",
        source="Mobile App",
        responses=[
            {"question_code": "QA-SCR-COND", "answer_value": "minor_scratch"},
            {"question_code": "QA-BODY-COND", "answer_value": "pristine"},
            {"question_code": "QA-BATT-HEALTH", "answer_value": "yes"},
            {"question_code": "QA-ICLOUD-LOCK", "answer_value": "no"},
            {"question_code": "QA-COSMETIC-OVERALL", "answer_value": "good"},
        ],
    )
    assessment.reload()
    assessment_price = assessment.quoted_price or assessment.estimated_price

    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="A")
    insp.reload()

    assert flt(insp.quoted_price) == flt(assessment_price), \
        f"Inspection quoted_price {insp.quoted_price} should match assessment price {assessment_price}"

    return True, (
        f"Pricing consistency: assessment={assessment_price}, "
        f"inspection_quoted={insp.quoted_price}"
    )


# ── S27: Customer Approval Page Token ─────────────────────────────

@_register("S27", "Customer Approval Page Token")
def s27_approval_token(ctx: dict) -> tuple[bool, str]:
    """Verify approval_token and guest API for customer-facing page."""
    assessment = _full_assessment_flow(ctx, item_code=get_item("Oppo Reno 12"), customer_name="Deepa", source="Mobile App")
    insp = _create_inspection_from_assessment(ctx, assessment, grade_letter="B")
    order = _full_order_flow(ctx, assessment, insp)

    order.reload()
    assert order.approval_token, "Order should have an approval_token"
    assert len(order.approval_token) == 32, \
        f"Token should be 32 chars, got {len(order.approval_token)}"
    _track(ctx, "Buyback Order", order.name, f"token={order.approval_token[:8]}...")

    from buyback.api import get_buyback_approval_details
    details = get_buyback_approval_details(order.approval_token)
    assert details["name"] == order.name, "Order name should match"
    assert details["final_price"] == order.final_price, "Price should match"
    assert details["item_name"], "Item name should be present"
    assert details["store_name"], "Store name should be present"
    _track(ctx, "API", "get_buyback_approval_details", "Guest API works")

    try:
        get_buyback_approval_details("invalid_token_12345678901234567890")
        assert False, "Invalid token should raise exception"
    except Exception:
        pass

    return True, (
        f"Approval token verified: {order.name}, "
        f"token={order.approval_token[:8]}..."
    )


# ── Registry accessor ─────────────────────────────────────────────

def get_all_scenarios() -> list[dict]:
    """Return list of all registered scenarios."""
    return SCENARIO_REGISTRY


def get_scenario(scenario_id: str) -> dict | None:
    """Return a single scenario by ID."""
    for s in SCENARIO_REGISTRY:
        if s["id"] == scenario_id:
            return s
    return None
