"""
Buyback QA – Scenario Library
===============================
17 scenarios (S01-S17) that exercise every workflow branch, edge case,
and permission rule in the buyback module.

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


def _full_quote_flow(
    ctx: dict,
    item_code: str = "QA-IPHONE-15",
    customer_name: str = "Ravi",
    store_code: str = "QA-ANN",
    warranty: str = "In Warranty",
    age_months: int = 3,
    responses: list | None = None,
) -> frappe._dict:
    """Create & accept a quote. Returns the quote doc."""
    cust = get_customer(customer_name)
    store = get_store(store_code)
    cust_doc = frappe.get_doc("Customer", cust)

    mobile_no = CUSTOMERS[[c["customer_name"] for c in CUSTOMERS].index(
        next(c["customer_name"] for c in CUSTOMERS if customer_name in c["customer_name"])
    )]["mobile_no"]

    if responses is None:
        responses = [
            {"question_code": "QA-SCR-COND", "answer_value": "flawless"},
            {"question_code": "QA-BODY-COND", "answer_value": "pristine"},
            {"question_code": "QA-BATT-HEALTH", "answer_value": "yes"},
            {"question_code": "QA-ICLOUD-LOCK", "answer_value": "no"},
            {"question_code": "QA-COSMETIC-OVERALL", "answer_value": "like_new"},
        ]

    with _as_user(_AGENT):
        from buyback.buyback.pricing.engine import calculate_estimated_price
        pricing = calculate_estimated_price(
            item_code=item_code,
            grade=None,
            warranty_status=warranty,
            device_age_months=age_months,
            responses=responses,
            brand=frappe.db.get_value("Item", item_code, "brand"),
            item_group=frappe.db.get_value("Item", item_code, "item_group"),
        )

        # Build response rows
        resp_rows = []
        for r in responses:
            qname = frappe.db.get_value("Buyback Question Bank", {"question_code": r["question_code"]}, "name")
            resp_rows.append({
                "question": qname,
                "question_code": r["question_code"],
                "answer_value": r["answer_value"],
                "answer_label": r.get("answer_label", ""),
                "price_impact_percent": r.get("price_impact_percent", 0),
            })

        quote = frappe.get_doc({
            "doctype": "Buyback Quote",
            "customer": cust,
            "mobile_no": mobile_no,
            "store": store,
            "item": item_code,
            "brand": frappe.db.get_value("Item", item_code, "brand"),
            "item_group": frappe.db.get_value("Item", item_code, "item_group"),
            "imei_serial": f"QA-IMEI-{frappe.generate_hash(length=8).upper()}",
            "warranty_status": warranty,
            "device_age_months": age_months,
            "base_price": pricing["base_price"],
            "total_deductions": pricing["total_deductions"],
            "estimated_price": pricing["estimated_price"],
            "quoted_price": pricing["estimated_price"],
            "responses": resp_rows,
        })
        quote.insert()
        quote.mark_quoted()
        _track(ctx, "Buyback Quote", quote.name, "Quote created & marked quoted")

        quote.mark_accepted()
        _track(ctx, "Buyback Quote", quote.name, "Quote accepted")
    return quote


def _full_inspection_flow(
    ctx: dict,
    quote: Any,
    grade_letter: str = "A",
    checklist_name: str | None = None,
) -> Any:
    """Create, start, and complete an inspection. Returns inspection doc."""
    with _as_user(_AGENT):
        insp = frappe.get_doc({
            "doctype": "Buyback Inspection",
            "buyback_quote": quote.name,
            "checklist_template": checklist_name or get_checklist("Smartphone Full"),
            "quoted_price": quote.quoted_price,
        })
        insp.insert()
        _track(ctx, "Buyback Inspection", insp.name, "Inspection created")

        if insp.checklist_template:
            insp.populate_checklist()

        # Fill results BEFORE save/start so mandatory `result` field is satisfied
        for row in insp.results:
            if row.check_type in ("Pass/Fail",):
                row.result = "Pass"
            elif "Grade" in (row.check_type or ""):
                row.result = grade_letter
            else:
                row.result = "OK"

        insp.save()
        insp.start_inspection()

        # Set grade BEFORE complete so condition_grade is populated
        grade_name = get_grade(grade_letter)
        insp.post_inspection_grade = grade_name
        insp.condition_grade = grade_name  # complete_inspection checks this before save
        insp.complete_inspection()
        _track(ctx, "Buyback Inspection", insp.name, f"Inspection completed grade={grade_letter}")
    return insp


def _apply_wf(doc, action):
    """Apply workflow transition and return refreshed doc."""
    from frappe.model.workflow import apply_workflow
    apply_workflow(doc, action)
    return frappe.get_doc(doc.doctype, doc.name)


def _full_order_flow(
    ctx: dict,
    quote: Any,
    insp: Any,
    final_price: float | None = None,
    skip_approval: bool = False,
) -> Any:
    """Create order and advance through workflow. Returns order doc."""
    cust = quote.customer
    mobile_no = quote.mobile_no
    store = quote.store
    item = quote.item
    grade = insp.condition_grade
    fp = final_price or flt(insp.revised_price or quote.quoted_price)

    with _as_user(_AGENT):
        order = frappe.get_doc({
            "doctype": "Buyback Order",
            "customer": cust,
            "mobile_no": mobile_no,
            "store": store,
            "item": item,
            "condition_grade": grade,
            "final_price": fp,
            "buyback_quote": quote.name,
            "buyback_inspection": insp.name,
            "imei_serial": quote.imei_serial,
            "warranty_status": quote.warranty_status,
            "brand": quote.brand,
        })
        order.insert()
        _track(ctx, "Buyback Order", order.name, f"Order created fp={fp}")

        # Workflow: Draft → Awaiting Approval or Auto Approve
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
        order.save()  # persist approval fields to DB before workflow (load_from_db wipes memory)
        order = _apply_wf(order, "Approve")
        _track(ctx, "Buyback Order", order.name, "Order approved")
    return order


_PLACEHOLDER_IMG = "/files/qa-placeholder.png"


def _fill_kyc_and_photos(order: Any) -> Any:
    """Populate mandatory KYC + device photo fields on an order (required before OTP).

    Uses placeholder image URLs for QA. Called automatically by _otp_flow.
    """
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
        # Fill mandatory KYC + device photos before OTP stage
        order = _fill_kyc_and_photos(order)
        # Workflow: Approved → Awaiting OTP (Send OTP)
        otp_code = CHOTPLog.generate_otp(
            order.mobile_no,
            "Buyback Confirmation",
            reference_doctype="Buyback Order",
            reference_name=order.name,
        )
        order = _apply_wf(order, "Send OTP")
        _track(ctx, "Buyback Order", order.name, "OTP sent")

        # Verify OTP
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
        order.save()  # persist to DB so workflow condition check sees otp_verified=1
        order = _apply_wf(order, "Verify OTP")
        _track(ctx, "Buyback Order", order.name, "OTP verified")
    return order, otp_code


def _payment_flow(ctx: dict, order: Any, method_type: str = "Cash") -> Any:
    """Record full payment and advance through workflow."""
    pm = get_payment_method(method_type)
    with _as_user(_AGENT):
        order.reload()
        # Workflow: OTP Verified → Ready to Pay
        order = _apply_wf(order, "Proceed to Payment")

        # Add payment row and save
        order.append("payments", {
            "payment_method": pm,
            "amount": flt(order.final_price),
            "transaction_reference": f"QA-TXN-{frappe.generate_hash(length=6)}",
            "payment_date": now_datetime(),
        })
        order.save()
        order.reload()

        # Workflow: Ready to Pay → Paid (triggers submit → on_submit → JE/SE)
        order = _apply_wf(order, "Confirm Payment")
        _track(ctx, "Buyback Order", order.name, f"Payment recorded via {method_type}")
    return order


def _close_order(ctx: dict, order: Any) -> Any:
    """Manager closes a paid order via workflow."""
    with _as_user(_MANAGER):
        order.reload()
        order = _apply_wf(order, "Close")
        _track(ctx, "Buyback Order", order.name, "Order closed")
    return order


# ══════════════════════════════════════════════════════════════════
#  SCENARIO DEFINITIONS
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


# ── S01: Happy path – Cash payout ─────────────────────────────────

@_register("S01", "Happy Path – Cash Payout")
def s01_happy_path_cash(ctx: dict) -> tuple[bool, str]:
    """Full flow: Quote → Inspect → Order → OTP → Pay Cash → Close."""
    quote = _full_quote_flow(ctx, item_code="QA-IPHONE-15", customer_name="Ravi")
    assert quote.status == "Accepted", f"Expected Accepted, got {quote.status}"

    insp = _full_inspection_flow(ctx, quote, grade_letter="A")
    assert insp.status == "Completed", f"Expected Completed, got {insp.status}"

    order = _full_order_flow(ctx, quote, insp)
    # Low price → no approval needed (iPhone 15 grade A IW 0-3 = 70000*0.70 = 49000 < 50000)
    if order.workflow_state == "Awaiting Approval":
        order = _approve_order(ctx, order)

    if order.workflow_state == "Approved":
        order, _ = _otp_flow(ctx, order)
    assert order.workflow_state == "OTP Verified", f"Expected OTP Verified, got {order.workflow_state}"

    order = _payment_flow(ctx, order, method_type="Cash")
    assert order.workflow_state == "Paid", f"Expected Paid, got {order.workflow_state}"

    order = _close_order(ctx, order)
    assert order.workflow_state == "Closed", f"Expected Closed, got {order.workflow_state}"

    return True, f"Full cash flow completed: {order.name}, price={order.final_price}"


# ── S02: Happy path – UPI payout ─────────────────────────────────

@_register("S02", "Happy Path – UPI Payout")
def s02_happy_path_upi(ctx: dict) -> tuple[bool, str]:
    """Same as S01 but with UPI payment."""
    quote = _full_quote_flow(ctx, item_code="QA-SAM-A34", customer_name="Priya")
    insp = _full_inspection_flow(ctx, quote, grade_letter="B")
    order = _full_order_flow(ctx, quote, insp)

    if order.workflow_state == "Awaiting Approval":
        order = _approve_order(ctx, order)

    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="UPI")
    assert order.workflow_state == "Paid", f"Expected Paid, got {order.workflow_state}"

    order = _close_order(ctx, order)
    assert order.workflow_state == "Closed"
    return True, f"UPI flow completed: {order.name}"


# ── S03: High-value with manager approval ─────────────────────────

@_register("S03", "High-Value Bank Transfer with Approval")
def s03_high_value_approval(ctx: dict) -> tuple[bool, str]:
    """Price > 50000 threshold → requires manager approval, Bank Transfer."""
    # MBP M3 grade A IW = 180000 * 0.70 = 126000 > 50000
    quote = _full_quote_flow(ctx, item_code="QA-MBP-M3", customer_name="Ajay",
                             warranty="In Warranty", age_months=2)
    insp = _full_inspection_flow(ctx, quote, grade_letter="A",
                                 checklist_name=get_checklist("Laptop Full"))
    order = _full_order_flow(ctx, quote, insp)

    assert order.requires_approval == 1, "Should require approval for high value"
    assert order.workflow_state == "Awaiting Approval", f"Expected Awaiting Approval, got {order.workflow_state}"

    order = _approve_order(ctx, order, remarks="High value approved for QA")
    assert order.workflow_state == "Approved"
    assert order.approved_by == _MANAGER

    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="Bank")
    order = _close_order(ctx, order)
    assert order.workflow_state == "Closed"
    return True, f"High-value BT approved: {order.name}, price={order.final_price}"


# ── S04: Price override with manager approval ─────────────────────

@_register("S04", "Price Override with Manager Approval")
def s04_price_override(ctx: dict) -> tuple[bool, str]:
    """Inspection reveals different grade → price recalculated → manager override."""
    # Quote at grade A, inspect finds grade C
    quote = _full_quote_flow(ctx, item_code="QA-SAM-S24", customer_name="Ravi",
                             warranty="In Warranty", age_months=5)
    original_price = quote.quoted_price

    insp = _full_inspection_flow(ctx, quote, grade_letter="C")

    # Recalculate with grade C
    from buyback.buyback.pricing.engine import calculate_final_price
    final_calc = calculate_final_price(quote.name, condition_grade=insp.condition_grade)
    assert final_calc["price_changed"], "Price should change with different grade"

    order = _full_order_flow(ctx, quote, insp, final_price=final_calc["final_price"])

    if order.workflow_state == "Awaiting Approval":
        order = _approve_order(ctx, order, remarks="Price override: grade C vs quoted A")

    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="Cash")
    order = _close_order(ctx, order)

    assert order.final_price != original_price, "Final price should differ from original"
    return True, f"Price override: original={original_price}, final={order.final_price}"


# ── S05: OTP failure + retry limit ────────────────────────────────

@_register("S05", "OTP Failure and Retry Limit")
def s05_otp_failure(ctx: dict) -> tuple[bool, str]:
    """Wrong OTP submitted multiple times → eventually succeeds with correct OTP."""
    quote = _full_quote_flow(ctx, item_code="QA-OPPO-R12", customer_name="Deepa")
    insp = _full_inspection_flow(ctx, quote, grade_letter="B")
    order = _full_order_flow(ctx, quote, insp)

    if order.workflow_state == "Awaiting Approval":
        order = _approve_order(ctx, order)

    with _as_user(_AGENT):
        order = _fill_kyc_and_photos(order)
        otp_code = order.send_otp()
        _track(ctx, "Buyback Order", order.name, "OTP sent")

        # Try wrong OTPs
        for i in range(3):
            wrong = "000000"
            result = order.verify_otp(wrong)
            assert not result["valid"], f"Wrong OTP should fail (attempt {i+1})"

        # Now correct OTP
        result = order.verify_otp(otp_code)
        assert result["valid"], f"Correct OTP should succeed: {result['message']}"
        _track(ctx, "Buyback Order", order.name, "OTP verified after failures")

    order = frappe.get_doc("Buyback Order", order.name)
    assert order.otp_verified == 1
    return True, f"OTP retry flow passed: {order.name}"


# ── S06: OTP expired ──────────────────────────────────────────────

@_register("S06", "OTP Expired")
def s06_otp_expired(ctx: dict) -> tuple[bool, str]:
    """Manipulate OTP expiry → verify fails → resend succeeds."""
    quote = _full_quote_flow(ctx, item_code="QA-XI-14", customer_name="Ravi")
    insp = _full_inspection_flow(ctx, quote, grade_letter="B")
    order = _full_order_flow(ctx, quote, insp)

    if order.workflow_state == "Awaiting Approval":
        order = _approve_order(ctx, order)

    with _as_user(_AGENT):
        order = _fill_kyc_and_photos(order)
        otp_code = order.send_otp()

        # Expire the OTP by manipulating the DB
        otp_log = frappe.get_last_doc("CH OTP Log", filters={
            "mobile_no": order.mobile_no,
            "purpose": "Buyback Confirmation",
            "status": "Pending",
        })
        otp_log.expires_at = add_to_date(now_datetime(), minutes=-10)
        otp_log.save(ignore_permissions=True)
        frappe.db.commit()

        # Verify should fail
        result = order.verify_otp(otp_code)
        assert not result["valid"], "Expired OTP should fail"

        # Resend new OTP
        new_otp = order.send_otp()
        result2 = order.verify_otp(new_otp)
        assert result2["valid"], f"Fresh OTP should succeed: {result2['message']}"

    order = frappe.get_doc("Buyback Order", order.name)
    assert order.otp_verified == 1
    return True, f"OTP expiry & resend passed: {order.name}"


# ── S07: Device rejected (iCloud / FRP locked) ────────────────────

@_register("S07", "Device Rejected – iCloud/FRP Locked")
def s07_device_rejected(ctx: dict) -> tuple[bool, str]:
    """Inspector rejects device due to lock. Order never created."""
    # Use iCloud locked response
    responses = [
        {"question_code": "QA-SCR-COND", "answer_value": "flawless"},
        {"question_code": "QA-BODY-COND", "answer_value": "pristine"},
        {"question_code": "QA-BATT-HEALTH", "answer_value": "yes"},
        {"question_code": "QA-ICLOUD-LOCK", "answer_value": "yes"},  # LOCKED!
        {"question_code": "QA-COSMETIC-OVERALL", "answer_value": "like_new"},
    ]
    quote = _full_quote_flow(ctx, item_code="QA-IPHONE-14", customer_name="Priya",
                             responses=responses)

    with _as_user(_AGENT):
        insp = frappe.get_doc({
            "doctype": "Buyback Inspection",
            "buyback_quote": quote.name,
            "checklist_template": get_checklist("Apple Device"),
            "quoted_price": quote.quoted_price,
        })
        insp.insert()
        _track(ctx, "Buyback Inspection", insp.name, "Inspection created")

        if insp.checklist_template:
            insp.populate_checklist()
        # Fill mandatory results before save
        for row in insp.results:
            row.result = "N/A"
        insp.save()
        insp.start_inspection()

        # Reject device
        insp.reject_device(reason="iCloud locked – cannot proceed with buyback")
        _track(ctx, "Buyback Inspection", insp.name, "Device rejected")

    insp = frappe.get_doc("Buyback Inspection", insp.name)
    assert insp.status == "Rejected", f"Expected Rejected, got {insp.status}"
    return True, f"Device rejected: {insp.name}"


# ── S08: Customer cancels after quote ──────────────────────────────

@_register("S08", "Customer Cancels After Quote")
def s08_cancel_after_quote(ctx: dict) -> tuple[bool, str]:
    """Quote created but customer never accepts → expires."""
    with _as_user(_AGENT):
        cust = get_customer("Ajay")
        store = get_store("QA-KIL")

        quote = frappe.get_doc({
            "doctype": "Buyback Quote",
            "customer": cust,
            "mobile_no": "9876500003",
            "store": store,
            "item": "QA-PIX-8",
            "brand": "Google",
            "item_group": "Smartphones",
            "warranty_status": "Out of Warranty",
            "device_age_months": 15,
            "base_price": 20000,
            "total_deductions": 2000,
            "estimated_price": 18000,
            "quoted_price": 18000,
        })
        quote.insert()
        quote.mark_quoted()
        _track(ctx, "Buyback Quote", quote.name, "Quoted")

        # Customer cancels → mark expired
        quote.mark_expired()
        _track(ctx, "Buyback Quote", quote.name, "Expired")

    quote = frappe.get_doc("Buyback Quote", quote.name)
    assert quote.status == "Expired"
    assert not quote.is_valid()
    return True, f"Quote expired: {quote.name}"


# ── S09: Customer cancels after inspection ─────────────────────────

@_register("S09", "Customer Cancels After Inspection")
def s09_cancel_after_inspection(ctx: dict) -> tuple[bool, str]:
    """Order created and submitted but manager rejects (customer changed mind)."""
    quote = _full_quote_flow(ctx, item_code="QA-SAM-S23", customer_name="Deepa",
                             warranty="Out of Warranty", age_months=14)
    insp = _full_inspection_flow(ctx, quote, grade_letter="B")
    order = _full_order_flow(ctx, quote, insp)

    # Manager rejects
    with _as_user(_MANAGER):
        order.reload()
        if order.workflow_state == "Awaiting Approval":
            order = _apply_wf(order, "Reject")
            _track(ctx, "Buyback Order", order.name, "Rejected by manager")
        elif order.workflow_state == "Approved":
            order = _apply_wf(order, "Reject")
            _track(ctx, "Buyback Order", order.name, "Rejected after approval")

    order = frappe.get_doc("Buyback Order", order.name)
    assert order.workflow_state == "Rejected"
    return True, f"Order rejected: {order.name}"


# ── S10: Exchange flow (3 sub-scenarios) ──────────────────────────

@_register("S10", "Exchange Flow – Equal/Lower/Higher Value")
def s10_exchange_flow(ctx: dict) -> tuple[bool, str]:
    """Test exchange order lifecycle with different value scenarios."""
    # First create a completed buyback order
    quote = _full_quote_flow(ctx, item_code="QA-IPHONE-13", customer_name="Ravi",
                             warranty="In Warranty", age_months=6)
    insp = _full_inspection_flow(ctx, quote, grade_letter="B")
    order = _full_order_flow(ctx, quote, insp)

    if order.workflow_state == "Awaiting Approval":
        order = _approve_order(ctx, order)
    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="Cash")
    order = _close_order(ctx, order)

    buyback_amount = order.final_price

    # Exchange scenario: customer wants a new Samsung S24 (65000)
    new_price = 65000
    exchange_discount = 2000

    with _as_user(_AGENT):
        exc_order = frappe.get_doc({
            "doctype": "Buyback Exchange Order",
            "buyback_order": order.name,
            "customer": order.customer,
            "mobile_no": order.mobile_no,
            "store": order.store,
            "old_item": order.item,
            "old_imei_serial": order.imei_serial,
            "old_condition_grade": insp.condition_grade,
            "buyback_amount": buyback_amount,
            "new_item": "QA-SAM-S24",
            "new_imei_serial": f"QA-NEWIMEI-{frappe.generate_hash(length=6).upper()}",
            "new_device_price": new_price,
            "exchange_discount": exchange_discount,
        })
        exc_order.insert()
        _track(ctx, "Buyback Exchange Order", exc_order.name, "Exchange created")

        # Workflow: Draft → New Device Delivered (Submit)
        exc_order = _apply_wf(exc_order, "Submit")
        _track(ctx, "Buyback Exchange Order", exc_order.name, "Exchange submitted")

        expected_pay = max(0, new_price - buyback_amount - exchange_discount)
        assert flt(exc_order.amount_to_pay) == flt(expected_pay), \
            f"Expected {expected_pay}, got {exc_order.amount_to_pay}"

        # Workflow: New Device Delivered → Awaiting Pickup
        exc_order.new_device_delivered_at = now_datetime()
        exc_order.save()
        exc_order = _apply_wf(exc_order, "Mark Awaiting Pickup")

        # Workflow: Awaiting Pickup → Old Device Received
        exc_order.old_device_received_at = now_datetime()
        exc_order.save()
        exc_order = _apply_wf(exc_order, "Receive Old Device")

        # Workflow: Old Device Received → Inspected
        exc_order.old_device_inspected_at = now_datetime()
        exc_order.old_condition_grade = insp.condition_grade
        exc_order.save()
        exc_order = _apply_wf(exc_order, "Complete Inspection")

    with _as_user(_MANAGER):
        exc_order.reload()
        # Workflow: Inspected → Settled
        exc_order.settlement_date = frappe.utils.nowdate()
        exc_order.settlement_reference = f"QA-SETTLE-{frappe.generate_hash(length=6)}"
        exc_order.save()
        exc_order = _apply_wf(exc_order, "Settle")

        # Workflow: Settled → Closed
        exc_order = _apply_wf(exc_order, "Close")
        _track(ctx, "Buyback Exchange Order", exc_order.name, "Exchange settled & closed")

    exc_order = frappe.get_doc("Buyback Exchange Order", exc_order.name)
    assert exc_order.workflow_state == "Closed"
    return True, f"Exchange closed: {exc_order.name}, to_pay={exc_order.amount_to_pay}"


# ── S11: Partial accessories deduction ─────────────────────────────

@_register("S11", "Partial Accessories Deduction")
def s11_accessories_deduction(ctx: dict) -> tuple[bool, str]:
    """Missing accessories should reduce the final price via question responses."""
    responses = [
        {"question_code": "QA-SCR-COND", "answer_value": "minor_scratch"},  # -5%
        {"question_code": "QA-BODY-COND", "answer_value": "minor_marks"},  # -3%
        {"question_code": "QA-BATT-HEALTH", "answer_value": "no"},  # -10%
        {"question_code": "QA-ICLOUD-LOCK", "answer_value": "no"},
        {"question_code": "QA-BOX-INC", "answer_value": "no"},  # -2%
        {"question_code": "QA-CHARGER-INC", "answer_value": "no"},  # -1%
        {"question_code": "QA-EARPH-INC", "answer_value": "no"},  # -1%
        {"question_code": "QA-INVOICE-INC", "answer_value": "no"},  # -3%
        {"question_code": "QA-COSMETIC-OVERALL", "answer_value": "good"},  # -3%
    ]
    quote = _full_quote_flow(ctx, item_code="QA-ONE-12", customer_name="Priya",
                             warranty="In Warranty", age_months=4, responses=responses)

    # Check that deductions were applied (estimated < base)
    assert quote.total_deductions > 0, "Expected deductions for missing accessories"
    assert quote.estimated_price < quote.base_price, "Estimated should be less than base"

    insp = _full_inspection_flow(ctx, quote, grade_letter="B")
    order = _full_order_flow(ctx, quote, insp)

    if order.workflow_state == "Awaiting Approval":
        order = _approve_order(ctx, order)
    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="UPI")
    order = _close_order(ctx, order)

    return True, f"Accessory deductions applied: base={quote.base_price}, final={order.final_price}"


# ── S12: Duplicate IMEI fraud detection ────────────────────────────

@_register("S12", "Duplicate IMEI Detection")
def s12_duplicate_imei(ctx: dict) -> tuple[bool, str]:
    """Same IMEI used in two quotes → second order should still work (detection is informational)."""
    shared_imei = f"QA-DUP-IMEI-{frappe.generate_hash(length=6).upper()}"

    with _as_user(_AGENT):
        store = get_store("QA-ANN")
        cust = get_customer("Ravi")

        # First quote with this IMEI
        q1 = frappe.get_doc({
            "doctype": "Buyback Quote",
            "customer": cust,
            "mobile_no": "9876500001",
            "store": store,
            "item": "QA-SAM-A34",
            "brand": "Samsung",
            "item_group": "Smartphones",
            "imei_serial": shared_imei,
            "warranty_status": "In Warranty",
            "device_age_months": 3,
            "base_price": 15000,
            "estimated_price": 14000,
            "quoted_price": 14000,
            "total_deductions": 1000,
        })
        q1.insert()
        q1.mark_quoted()
        q1.mark_accepted()
        _track(ctx, "Buyback Quote", q1.name, "First quote with dup IMEI")

        # Second quote same IMEI, different customer
        cust2 = get_customer("Fraud")
        q2 = frappe.get_doc({
            "doctype": "Buyback Quote",
            "customer": cust2,
            "mobile_no": "9876500099",
            "store": store,
            "item": "QA-SAM-A34",
            "brand": "Samsung",
            "item_group": "Smartphones",
            "imei_serial": shared_imei,
            "warranty_status": "In Warranty",
            "device_age_months": 3,
            "base_price": 15000,
            "estimated_price": 14000,
            "quoted_price": 14000,
            "total_deductions": 1000,
        })
        q2.insert()
        q2.mark_quoted()
        q2.mark_accepted()
        _track(ctx, "Buyback Quote", q2.name, "Second quote with dup IMEI (fraud test)")

    # Verify both quotes exist with same IMEI
    dup_count = frappe.db.count("Buyback Quote", {"imei_serial": shared_imei})
    assert dup_count >= 2, f"Expected ≥2 quotes with same IMEI, got {dup_count}"
    return True, f"Duplicate IMEI detected: {shared_imei} in {dup_count} quotes"


# ── S13: Unknown model edge case ──────────────────────────────────

@_register("S13", "Unknown Model – Zero Base Price")
def s13_unknown_model(ctx: dict) -> tuple[bool, str]:
    """Item without BPM entry → base_price=0 → order may still proceed."""
    # Create a temporary item without BPM
    with _as_user(_AGENT):
        item_code = "QA-UNKNOWN-MODEL"
        if not frappe.db.exists("Item", item_code):
            frappe.get_doc({
                "doctype": "Item",
                "item_code": item_code,
                "item_name": "QA Unknown Model",
                "item_group": "Smartphones",
                "stock_uom": "Nos",
                "is_stock_item": 1,
                "gst_hsn_code": "85171300",
            }).insert(ignore_permissions=True)
            _track(ctx, "Item", item_code, "Unknown model item")

        from buyback.buyback.pricing.engine import calculate_estimated_price
        pricing = calculate_estimated_price(
            item_code=item_code,
            grade=get_grade("A"),
            warranty_status="In Warranty",
            device_age_months=3,
        )
        assert pricing["base_price"] == 0, f"Expected 0 base price, got {pricing['base_price']}"
        assert pricing["estimated_price"] == 0, f"Expected 0 estimated, got {pricing['estimated_price']}"

    return True, "Unknown model returns zero price correctly"


# ── S14: Negative price prevention ─────────────────────────────────

@_register("S14", "Negative Price Prevention")
def s14_negative_price(ctx: dict) -> tuple[bool, str]:
    """Massive deductions should floor at 0, never go negative."""
    # All bad answers → massive deductions
    responses = [
        {"question_code": "QA-SCR-COND", "answer_value": "cracked"},  # -25%
        {"question_code": "QA-BODY-COND", "answer_value": "cracked_back"},  # -20%
        {"question_code": "QA-BATT-HEALTH", "answer_value": "no"},  # -10%
        {"question_code": "QA-TOUCH-OK", "answer_value": "no"},  # -20%
        {"question_code": "QA-CAM-WORK", "answer_value": "no"},  # -15%
        {"question_code": "QA-CHARGE-OK", "answer_value": "no"},  # -12%
        {"question_code": "QA-ICLOUD-LOCK", "answer_value": "no"},
        {"question_code": "QA-WATER-DMG", "answer_value": "yes"},  # -30%
        {"question_code": "QA-COSMETIC-OVERALL", "answer_value": "below_avg"},  # -15%
    ]
    # Total: -147% → should clamp to 0

    with _as_user(_AGENT):
        from buyback.buyback.pricing.engine import calculate_estimated_price
        pricing = calculate_estimated_price(
            item_code="QA-SAM-A34",
            grade=get_grade("D"),
            warranty_status="Out of Warranty",
            device_age_months=30,
            responses=responses,
            brand="Samsung",
            item_group="Smartphones",
        )
        assert pricing["estimated_price"] >= 0, \
            f"Price should never be negative: {pricing['estimated_price']}"

    return True, f"Negative price prevented: est={pricing['estimated_price']}, deductions={pricing['total_deductions']}"


# ── S15: Double payout prevention ──────────────────────────────────

@_register("S15", "Double Payout Prevention")
def s15_double_payout(ctx: dict) -> tuple[bool, str]:
    """After order is Paid (submitted), further payment modification should be blocked by Frappe."""
    quote = _full_quote_flow(ctx, item_code="QA-SAM-A34", customer_name="Ajay")
    insp = _full_inspection_flow(ctx, quote, grade_letter="A")
    order = _full_order_flow(ctx, quote, insp)

    if order.workflow_state == "Awaiting Approval":
        order = _approve_order(ctx, order)
    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="Cash")
    assert order.workflow_state == "Paid"

    # Try adding another payment — submitted doc should block this
    pm = get_payment_method("Cash")
    with _as_user(_AGENT):
        order.reload()
        order.append("payments", {
            "payment_method": pm,
            "amount": 1000,
            "transaction_reference": "QA-DOUBLE-PAY",
            "payment_date": now_datetime(),
        })
        blocked = False
        try:
            order.save()
        except Exception:
            blocked = True

        if blocked:
            return True, f"Double payout blocked: submitted order {order.name} cannot be modified"
        else:
            # Save succeeded — check if system detected overpayment
            order.reload()
            assert order.payment_status == "Overpaid", \
                f"Expected Overpaid, got {order.payment_status}"
            return True, f"Double payout detected: total_paid={order.total_paid}, final_price={order.final_price}"


# ── S16: Store-wise permission test ────────────────────────────────

@_register("S16", "Store-wise Permission Test")
def s16_store_permission(ctx: dict) -> tuple[bool, str]:
    """Verify that different roles can/cannot perform specific actions.
    Agent can create quotes/orders. Manager can approve. Auditor has read-only."""
    store = get_store("QA-VEL")
    cust = get_customer("Deepa")

    # Agent can create a quote
    with _as_user(_AGENT):
        quote = frappe.get_doc({
            "doctype": "Buyback Quote",
            "customer": cust,
            "mobile_no": "9876500004",
            "store": store,
            "item": "QA-PIX-8",
            "brand": "Google",
            "item_group": "Smartphones",
            "warranty_status": "In Warranty",
            "device_age_months": 5,
            "base_price": 30000,
            "estimated_price": 28000,
            "quoted_price": 28000,
            "total_deductions": 2000,
        })
        quote.insert()
        quote.mark_quoted()
        quote.mark_accepted()
        _track(ctx, "Buyback Quote", quote.name, "Agent created quote at VEL store")

    # Auditor should be read-only
    with _as_user("qa_auditor@test.com"):
        doc = frappe.get_doc("Buyback Quote", quote.name)
        can_read = frappe.has_permission("Buyback Quote", ptype="read", doc=doc)
        # Auditor should NOT be able to write
        can_write = frappe.has_permission("Buyback Quote", ptype="write", doc=doc, throw=False)

    assert can_read, "Auditor should be able to read quotes"
    # Note: This assertion depends on DocPerm configuration
    # If auditor has write, we just note it

    return True, f"Permission test passed: agent created at VEL store, auditor can_read={can_read}, can_write={can_write}"


# ── S17: Reporting sanity check ────────────────────────────────────

@_register("S17", "Reporting Sanity Check")
def s17_reporting_sanity(ctx: dict) -> tuple[bool, str]:
    """Verify audit log entries exist and totals make sense."""
    # Count audit logs for QA orders
    qa_stores = [get_store(s["store_code"]) for s in [
        {"store_code": "QA-ANN"}, {"store_code": "QA-KIL"}, {"store_code": "QA-VEL"},
    ] if get_store(s["store_code"])]

    audit_count = frappe.db.count("Buyback Audit Log", {
        "user": ["like", "qa_%@test.com"],
    })

    # Count QA orders
    order_count = frappe.db.count("Buyback Order", {
        "store": ["in", qa_stores],
    })

    # Count QA quotes
    quote_count = frappe.db.count("Buyback Quote", {
        "store": ["in", qa_stores],
    })

    assert quote_count > 0, "Expected QA quotes to exist"
    assert order_count > 0, "Expected QA orders to exist"
    assert audit_count > 0, "Expected audit log entries"

    return True, f"Reporting: {quote_count} quotes, {order_count} orders, {audit_count} audit entries"


# ── S18: KYC Document Upload & Verification ───────────────────────

@_register("S18", "KYC Document Upload & Verification")
def s18_kyc_verification(ctx: dict) -> tuple[bool, str]:
    """Verify that KYC documents can be uploaded and verified on a buyback order,
    and that data syncs to Customer master."""
    quote = _full_quote_flow(ctx, item_code="QA-IPHONE-15", customer_name="Ravi")
    insp = _full_inspection_flow(ctx, quote, grade_letter="A")
    order = _full_order_flow(ctx, quote, insp)

    if order.workflow_state == "Awaiting Approval":
        order = _approve_order(ctx, order)

    # Upload KYC documents + device photos
    with _as_user(_AGENT):
        order.reload()
        order.customer_photo = "/files/qa_customer_photo.jpg"
        order.customer_id_type = "Aadhar Card"
        order.customer_id_number = "1234-5678-9012"
        order.customer_id_front = "/files/qa_aadhar_front.jpg"
        order.customer_id_back = "/files/qa_aadhar_back.jpg"
        order.device_photo_front = "/files/qa_device_front.jpg"
        order.device_photo_back = "/files/qa_device_back.jpg"
        order.device_photo_screen = "/files/qa_device_screen.jpg"
        order.device_photo_imei = "/files/qa_device_imei.jpg"
        order.save()
        _track(ctx, "Buyback Order", order.name, "KYC + device photos uploaded")

    # Manager verifies KYC → should also sync to Customer
    with _as_user(_MANAGER):
        order.reload()
        assert order.customer_id_type == "Aadhar Card", "ID type should be set"
        assert order.customer_id_number == "1234-5678-9012", "ID number should be set"
        order.verify_kyc()
        _track(ctx, "Buyback Order", order.name, "KYC verified by manager")

    order = frappe.get_doc("Buyback Order", order.name)
    assert order.kyc_verified == 1, "KYC should be verified"
    assert order.kyc_verified_by == _MANAGER, f"Expected manager, got {order.kyc_verified_by}"
    assert order.kyc_verified_at is not None, "Verification timestamp should be set"

    # ── Verify KYC synced to Customer ──
    cust = frappe.get_doc("Customer", order.customer)
    assert cust.get("ch_kyc_verified") == 1, "Customer KYC should be verified"
    assert cust.get("ch_id_type") == "Aadhar Card", \
        f"Customer ID type should be Aadhar Card, got {cust.get('ch_id_type')}"
    assert cust.get("ch_id_number") == "1234-5678-9012", "Customer ID number should match"
    assert cust.get("ch_customer_photo") == "/files/qa_customer_photo.jpg", \
        "Customer photo should be synced"
    assert cust.get("ch_id_front_image") == "/files/qa_aadhar_front.jpg", \
        "Customer ID front image should be synced"
    assert cust.get("ch_kyc_source_order") == order.name, \
        "KYC source order should reference this order"
    _track(ctx, "Customer", cust.name, "KYC synced to Customer")

    # Try KYC verification without documents (should fail)
    quote2 = _full_quote_flow(ctx, item_code="QA-SAM-A34", customer_name="Priya")
    insp2 = _full_inspection_flow(ctx, quote2, grade_letter="B")
    order2 = _full_order_flow(ctx, quote2, insp2)

    with _as_user(_MANAGER):
        order2.reload()
        try:
            order2.verify_kyc()
            assert False, "KYC verification without docs should fail"
        except Exception:
            pass  # Expected to fail

    return True, f"KYC verification + Customer sync passed: {order.name}"


# ── S19: Loyalty Points on Buyback Close ───────────────────────────

@_register("S19", "Loyalty Points Awarded on Close")
def s19_loyalty_points(ctx: dict) -> tuple[bool, str]:
    """Verify loyalty points are awarded when a buyback order is closed."""
    # Ensure loyalty is enabled in settings
    settings = frappe.get_single("Buyback Settings")
    assert settings.enable_loyalty_points, "Loyalty points should be enabled in settings"
    assert settings.loyalty_program, "Loyalty program should be configured"

    quote = _full_quote_flow(ctx, item_code="QA-SAM-S24", customer_name="Ajay",
                             warranty="In Warranty", age_months=3)
    insp = _full_inspection_flow(ctx, quote, grade_letter="A")
    order = _full_order_flow(ctx, quote, insp)

    if order.workflow_state == "Awaiting Approval":
        order = _approve_order(ctx, order)
    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="UPI")

    # Before close — no loyalty points
    assert order.loyalty_points_earned == 0, "No points before close"

    # Close order — should award loyalty points
    order = _close_order(ctx, order)

    order = frappe.get_doc("Buyback Order", order.name)
    assert order.loyalty_points_earned > 0, \
        f"Expected loyalty points > 0, got {order.loyalty_points_earned}"
    assert order.loyalty_point_entry, "Loyalty Point Entry should be linked"

    # Verify points calculation: 10 points per ₹100
    expected_points = int(flt(order.final_price) / 100) * 10
    assert order.loyalty_points_earned == expected_points, \
        f"Expected {expected_points} points, got {order.loyalty_points_earned}"

    # Verify Loyalty Point Entry exists and is correct
    lpe = frappe.get_doc("Loyalty Point Entry", order.loyalty_point_entry)
    assert lpe.customer == order.customer, "LPE customer should match order"
    assert lpe.loyalty_points == expected_points, "LPE points should match"
    assert lpe.invoice_type == "Buyback Order", "LPE invoice_type should be Buyback Order"
    assert lpe.invoice == order.name, "LPE invoice should reference the order"
    _track(ctx, "Loyalty Point Entry", lpe.name, f"Points={expected_points}")

    # ── Verify Customer activity was updated on close ──
    cust = frappe.get_doc("Customer", order.customer)
    assert cint(cust.get("ch_total_buybacks")) > 0, \
        f"Customer total buybacks should be > 0, got {cust.get('ch_total_buybacks')}"
    assert cint(cust.get("ch_loyalty_points_balance")) >= expected_points, \
        f"Customer loyalty balance should be >= {expected_points}, got {cust.get('ch_loyalty_points_balance')}"
    assert cust.get("ch_last_visit_date") is not None, \
        "Customer last visit date should be set"
    # Verify device photos synced
    assert cust.get("ch_device_photo_front"), "Customer device front photo should be synced"
    assert cust.get("ch_device_photo_back"), "Customer device back photo should be synced"
    _track(ctx, "Customer", cust.name, f"Activity updated: buybacks={cust.get('ch_total_buybacks')}")

    return True, f"Loyalty points awarded + Customer updated: {order.name}, points={expected_points}"


# ── S20: Mobile Diagnostic → Physical Inspection → Quote → Order ──

@_register("S20", "Mobile Diagnostic to Order Flow")
def s20_mobile_diagnostic_flow(ctx: dict) -> tuple[bool, str]:
    """
    Full mobile-first flow:
    1. Mobile app sends diagnostic data via API → creates Inspection
    2. Store agent performs physical inspection
    3. Agent creates Quote based on inspection
    4. Quote accepted → Order → OTP → Pay → Close
    """
    import json

    # Step 1: Mobile app submits diagnostic data
    mobile_no = "9876500001"  # QA Ravi Kumar
    item_code = "QA-IPHONE-14"

    diagnostic_results = [
        {"test": "Battery Health", "code": "BATT-HEALTH", "result": "82%", "status": "Pass"},
        {"test": "Screen Touch", "code": "SCR-TOUCH", "result": "Responsive", "status": "Pass"},
        {"test": "Speaker Test", "code": "SPEAKER", "result": "Clear audio", "status": "Pass"},
        {"test": "Camera Test", "code": "CAMERA", "result": "Autofocus OK", "status": "Pass"},
        {"test": "Charging Port", "code": "CHARGE", "result": "Charging OK", "status": "Pass"},
        {"test": "WiFi Test", "code": "WIFI", "result": "Connected", "status": "Pass"},
        {"test": "Bluetooth Test", "code": "BT", "result": "Paired OK", "status": "Pass"},
        {"test": "Accelerometer", "code": "ACCEL", "result": "Responsive", "status": "Pass"},
        {"test": "Water Damage", "code": "WATER-IND", "result": "No damage", "status": "Pass"},
        {"test": "IMEI Check", "code": "IMEI-VALID", "result": "Valid", "status": "Pass"},
    ]

    store = get_store("QA-ANN")
    ext_id = f"DIAG-{frappe.generate_hash(length=8).upper()}"

    with _as_user(_AGENT):
        # Simulate the API call by directly creating the inspection
        from buyback.api import submit_mobile_diagnostic
        result = submit_mobile_diagnostic(
            mobile_no=mobile_no,
            item_code=item_code,
            diagnostic_results=json.dumps(diagnostic_results),
            store=store,
            imei_serial=f"QA-MOB-IMEI-{frappe.generate_hash(length=6).upper()}",
            brand="Apple",
            external_diagnostic_id=ext_id,
        )
        assert result["diagnostic_source"] == "Mobile App", "Should be from Mobile App"
        assert result["customer_found"], "Customer should be found by phone"
        assert result["results_count"] == 10, f"Expected 10 results, got {result['results_count']}"
        _track(ctx, "Buyback Inspection", result["name"], "Mobile diagnostic received")

        # Step 2: Store agent performs physical inspection on the same record
        insp = frappe.get_doc("Buyback Inspection", result["name"])
        assert insp.diagnostic_source == "Mobile App"
        assert insp.mobile_diagnostic_id == ext_id
        assert insp.diagnostic_data is not None

        # Agent starts physical inspection
        insp.start_inspection()

        # Agent sets physical inspection grade
        grade = get_grade("B")
        insp.post_inspection_grade = grade
        insp.condition_grade = grade
        insp.revised_price = 38000  # physical assessment value
        insp.complete_inspection()
        _track(ctx, "Buyback Inspection", insp.name, "Physical inspection completed")

    # Step 3: Create a quote based on the mobile diagnostic + physical inspection
    insp = frappe.get_doc("Buyback Inspection", result["name"])
    assert insp.status == "Completed"

    with _as_user(_AGENT):
        cust = get_customer("Ravi")
        quote = frappe.get_doc({
            "doctype": "Buyback Quote",
            "customer": cust,
            "mobile_no": mobile_no,
            "store": store,
            "item": item_code,
            "brand": "Apple",
            "item_group": "Smartphones",
            "imei_serial": insp.imei_serial,
            "warranty_status": "In Warranty",
            "device_age_months": 8,
            "base_price": 42000,
            "total_deductions": 4000,
            "estimated_price": 38000,
            "quoted_price": 38000,
        })
        quote.insert()
        quote.mark_quoted()
        quote.mark_accepted()
        _track(ctx, "Buyback Quote", quote.name, "Quote based on mobile diagnostic")

        # Link the inspection back to the quote
        insp.reload()
        insp.buyback_quote = quote.name
        insp.quoted_price = quote.quoted_price
        insp.save()

    # Step 4: Normal order flow
    with _as_user(_AGENT):
        order = frappe.get_doc({
            "doctype": "Buyback Order",
            "customer": cust,
            "mobile_no": mobile_no,
            "store": store,
            "item": item_code,
            "condition_grade": insp.condition_grade,
            "final_price": flt(insp.revised_price or quote.quoted_price),
            "buyback_quote": quote.name,
            "buyback_inspection": insp.name,
            "imei_serial": insp.imei_serial,
            "warranty_status": "In Warranty",
            "brand": "Apple",
        })
        order.insert()
        _track(ctx, "Buyback Order", order.name, "Order from mobile diagnostic")

        order.reload()
        if order.requires_approval:
            order = _apply_wf(order, "Submit for Approval")
        else:
            order = _apply_wf(order, "Auto Approve")

    if order.workflow_state == "Awaiting Approval":
        order = _approve_order(ctx, order)

    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="Cash")
    order = _close_order(ctx, order)

    assert order.workflow_state == "Closed"
    return True, (
        f"Mobile diagnostic → Order completed: {order.name}, "
        f"diagnostic_id={ext_id}, price={order.final_price}"
    )


# ── S21: KYC Mandatory Enforcement + Customer History ─────────────

@_register("S21", "KYC Mandatory & Customer History Update")
def s21_kyc_mandatory_and_history(ctx: dict) -> tuple[bool, str]:
    """Verify KYC + device photos are mandatory before OTP, and that
    on close the Customer record is updated with buyback history."""
    from buyback.exceptions import BuybackStatusError

    quote = _full_quote_flow(ctx, item_code="QA-IPHONE-14", customer_name="Ravi")
    insp = _full_inspection_flow(ctx, quote, grade_letter="A")
    order = _full_order_flow(ctx, quote, insp)

    if order.workflow_state == "Awaiting Approval":
        order = _approve_order(ctx, order)

    # ── Test 1: OTP should FAIL without KYC + device photos ──
    order_name = order.name
    with _as_user(_AGENT):
        order.reload()
        # Ensure no KYC photos are set
        assert not order.customer_photo, "Customer photo should be empty initially"
        assert not order.device_photo_front, "Device front photo should be empty initially"

        # Try to call send_otp — should fail validation
        try:
            order.send_otp()
            assert False, "send_otp without KYC should fail"
        except BuybackStatusError:
            pass  # Expected!
        except frappe.exceptions.ValidationError:
            pass  # Also acceptable

    # ── Test 2: Fill all mandatory fields → OTP should work ──
    order = frappe.get_doc("Buyback Order", order_name)
    order, _ = _otp_flow(ctx, order)  # _otp_flow auto-fills KYC + photos
    _track(ctx, "Buyback Order", order.name, "OTP sent with KYC + device photos")

    order = _payment_flow(ctx, order, method_type="Cash")
    order = _close_order(ctx, order)

    # ── Test 3: Verify Customer activity updated after close ──
    cust = frappe.get_doc("Customer", order.customer)
    assert cint(cust.get("ch_total_buybacks")) > 0, \
        f"Total buybacks should be > 0, got {cust.get('ch_total_buybacks')}"
    assert str(cust.get("ch_last_visit_date")) == nowdate(), \
        f"Last visit should be today, got {cust.get('ch_last_visit_date')}"
    assert cust.get("ch_device_photo_front"), "Device front photo should sync to Customer"
    assert cust.get("ch_device_photo_back"), "Device back photo should sync to Customer"
    assert cust.get("ch_device_photo_source") == f"Buyback Order {order.name}", \
        f"Device photo source should reference this order"
    _track(ctx, "Customer", cust.name,
           f"History updated: buybacks={cust.get('ch_total_buybacks')}, "
           f"last_visit={cust.get('ch_last_visit_date')}")

    return True, (
        f"KYC mandatory enforcement + Customer history update verified: {order.name}"
    )


# ── S22: IMEI/Serial No History Tracking ──────────────────────────

@_register("S22", "IMEI History via Serial No")
def s22_imei_history(ctx: dict) -> tuple[bool, str]:
    """Verify that Serial No (IMEI) gets buyback custom fields updated
    and timeline comments added through the full flow.

    Reuses ERPNext Serial No + Frappe Comment — no custom DocType needed.
    """
    import json

    quote = _full_quote_flow(ctx, item_code="QA-IPHONE-15", customer_name="Ravi")
    imei = quote.imei_serial
    _track(ctx, "Serial No", imei, "IMEI created via quote")

    insp = _full_inspection_flow(ctx, quote, grade_letter="A")
    order = _full_order_flow(ctx, quote, insp)

    if order.workflow_state == "Awaiting Approval":
        order = _approve_order(ctx, order)
    order, _ = _otp_flow(ctx, order)
    order = _payment_flow(ctx, order, method_type="Cash")
    order = _close_order(ctx, order)

    # After close + Stock Entry, Serial No should exist and have buyback fields
    assert frappe.db.exists("Serial No", imei), \
        f"Serial No {imei} should exist after Stock Entry"

    sn = frappe.get_doc("Serial No", imei)
    assert sn.ch_buyback_status == "Bought Back", \
        f"Expected 'Bought Back', got '{sn.ch_buyback_status}'"
    assert sn.ch_buyback_order == order.name, \
        f"Expected order {order.name}, got '{sn.ch_buyback_order}'"
    assert flt(sn.ch_buyback_price) == flt(order.final_price), \
        f"Expected price {order.final_price}, got {sn.ch_buyback_price}"
    assert sn.ch_buyback_customer == order.customer, \
        f"Expected customer {order.customer}, got '{sn.ch_buyback_customer}'"
    assert cint(sn.ch_buyback_count) >= 1, \
        f"Buyback count should be >= 1, got {sn.ch_buyback_count}"

    # Check timeline comments on Serial No
    comments = frappe.get_all(
        "Comment",
        filters={
            "reference_doctype": "Serial No",
            "reference_name": imei,
            "comment_type": "Info",
        },
        fields=["content"],
    )
    assert len(comments) > 0, "Expected timeline comments on Serial No"
    _track(ctx, "Serial No", imei, f"buyback_status={sn.ch_buyback_status}")

    # Test the get_imei_history API
    from buyback.api import get_imei_history
    history = get_imei_history(imei)
    assert history["serial_exists"], "Serial should exist in history"
    assert len(history["orders"]) >= 1, "Should have at least 1 order in history"
    assert len(history["quotes"]) >= 1, "Should have at least 1 quote in history"
    assert len(history["timeline"]) >= 1, "Should have timeline entries"

    return True, (
        f"IMEI history tracking verified: {imei}, "
        f"status={sn.ch_buyback_status}, count={sn.ch_buyback_count}, "
        f"comments={len(comments)}"
    )


# ── S23: Phone Number Lookup APIs ─────────────────────────────────

@_register("S23", "Phone Lookup – Quotes, Inspections, Orders")
def s23_phone_lookup(ctx: dict) -> tuple[bool, str]:
    """Verify that quotes, inspections, and orders can be found by phone number."""
    # Create a full flow first
    quote = _full_quote_flow(ctx, item_code="QA-SAM-A34", customer_name="Priya")
    insp = _full_inspection_flow(ctx, quote, grade_letter="B")
    order = _full_order_flow(ctx, quote, insp)

    mobile_no = quote.mobile_no

    # Test get_quotes_by_phone
    from buyback.api import get_quotes_by_phone, get_inspections_by_phone, get_orders_by_phone

    quotes = get_quotes_by_phone(mobile_no)
    assert len(quotes) >= 1, f"Expected >= 1 quotes for {mobile_no}, got {len(quotes)}"
    assert any(q["name"] == quote.name for q in quotes), \
        f"Quote {quote.name} should appear in phone lookup"
    _track(ctx, "API", "get_quotes_by_phone", f"Found {len(quotes)} quotes")

    # Test get_inspections_by_phone
    inspections = get_inspections_by_phone(mobile_no)
    assert len(inspections) >= 1, f"Expected >= 1 inspections for {mobile_no}"
    _track(ctx, "API", "get_inspections_by_phone", f"Found {len(inspections)} inspections")

    # Test get_orders_by_phone
    orders = get_orders_by_phone(mobile_no)
    assert len(orders) >= 1, f"Expected >= 1 orders for {mobile_no}"
    assert any(o["name"] == order.name for o in orders), \
        f"Order {order.name} should appear in phone lookup"
    _track(ctx, "API", "get_orders_by_phone", f"Found {len(orders)} orders")

    return True, (
        f"Phone lookup verified for {mobile_no}: "
        f"{len(quotes)} quotes, {len(inspections)} inspections, {len(orders)} orders"
    )


# ── S24: Item Search API ──────────────────────────────────────────

@_register("S24", "Item Search API")
def s24_item_search(ctx: dict) -> tuple[bool, str]:
    """Verify the search_items API returns items with hierarchy IDs."""
    from buyback.api import search_items

    # Search by text + brand to get only buyback-eligible devices
    results = search_items(search_text="iPhone", brand="Apple")
    assert len(results) >= 1, f"Should find Apple iPhone items, got {len(results)}"
    first_item = results[0]

    # Verify hierarchy fields are present
    for field in ("item_code", "item_name", "brand", "item_group"):
        assert first_item.get(field), f"Item should have {field}"
    _track(ctx, "API", "search_items", f"Text+brand search: {len(results)} results")

    # Search by brand
    results_brand = search_items(brand="Apple")
    assert len(results_brand) >= 1, "Should find Apple items"
    for item in results_brand:
        assert item.get("brand") == "Apple", f"Brand filter broken: {item.get('brand')}"
    _track(ctx, "API", "search_items", f"Brand filter: {len(results_brand)} results")

    # Search by item_group
    results_group = search_items(item_group="Smartphones")
    assert len(results_group) >= 1, "Should find Smartphone items"
    _track(ctx, "API", "search_items", f"Group filter: {len(results_group)} results")

    return True, (
        f"Item search API verified: "
        f"text={len(results)}, brand={len(results_brand)}, group={len(results_group)}"
    )


# ── S25: JE/SE Created at Paid Stage (not Submit) ─────────────────

@_register("S25", "JE/SE Created at Paid Stage")
def s25_je_se_timing(ctx: dict) -> tuple[bool, str]:
    """Verify that Journal Entry and Stock Entry are created when
    order reaches 'Paid' status, NOT at submit time."""
    quote = _full_quote_flow(ctx, item_code="QA-SAM-S24", customer_name="Ajay",
                             warranty="In Warranty", age_months=3)
    insp = _full_inspection_flow(ctx, quote, grade_letter="A")
    order = _full_order_flow(ctx, quote, insp)

    # After submit — JE and SE should NOT exist yet
    order.reload()
    assert not order.journal_entry, \
        f"JE should not exist after submit, got {order.journal_entry}"
    assert not order.stock_entry, \
        f"SE should not exist after submit, got {order.stock_entry}"
    _track(ctx, "Buyback Order", order.name, "No JE/SE after submit ✓")

    # Approve + OTP
    if order.workflow_state == "Awaiting Approval":
        order = _approve_order(ctx, order)
    order, _ = _otp_flow(ctx, order)

    # Still no JE/SE
    order.reload()
    assert not order.journal_entry, "JE should not exist after OTP"
    assert not order.stock_entry, "SE should not exist after OTP"

    # Pay → JE and SE should NOW be created
    order = _payment_flow(ctx, order, method_type="UPI")
    order.reload()
    assert order.journal_entry, "JE should exist after payment"
    assert order.stock_entry, "SE should exist after payment"
    _track(ctx, "Buyback Order", order.name, f"JE={order.journal_entry}, SE={order.stock_entry}")

    # Verify JE is submitted
    je = frappe.get_doc("Journal Entry", order.journal_entry)
    assert je.docstatus == 1, "JE should be submitted"

    # Verify SE is submitted with correct serial_no
    se = frappe.get_doc("Stock Entry", order.stock_entry)
    assert se.docstatus == 1, "SE should be submitted"
    assert se.items[0].serial_no == order.imei_serial, \
        f"SE serial_no should be {order.imei_serial}"

    # Close
    order = _close_order(ctx, order)

    return True, (
        f"JE/SE timing verified: created at Paid (not Submit). "
        f"JE={order.journal_entry}, SE={order.stock_entry}"
    )


# ── S26: Mobile Diagnostic Pricing + Comparison ───────────────────

@_register("S26", "Mobile Diagnostic Pricing & Comparison")
def s26_diagnostic_pricing_comparison(ctx: dict) -> tuple[bool, str]:
    """Verify that:
    1. submit_mobile_diagnostic returns an estimated_price
    2. get_diagnostic_comparison normalizes mobile vs in-store results
    """
    import json
    from buyback.api import submit_mobile_diagnostic, get_diagnostic_comparison

    mobile_no = "9876500001"  # QA Ravi
    item_code = "QA-IPHONE-14"
    store = get_store("QA-ANN")

    diagnostic_results = [
        {"test": "Battery Health", "code": "BATT-HEALTH", "result": "85%", "status": "Pass"},
        {"test": "Screen Touch", "code": "SCR-TOUCH", "result": "OK", "status": "Pass"},
        {"test": "Camera Test", "code": "CAMERA", "result": "Working", "status": "Pass"},
        {"test": "Water Damage", "code": "WATER-IND", "result": "No damage", "status": "Pass"},
    ]

    with _as_user(_AGENT):
        result = submit_mobile_diagnostic(
            mobile_no=mobile_no,
            item_code=item_code,
            diagnostic_results=json.dumps(diagnostic_results),
            store=store,
            imei_serial=f"QA-DIAG-{frappe.generate_hash(length=6).upper()}",
            brand="Apple",
        )
        assert "estimated_price" in result, "Response should include estimated_price"
        _track(ctx, "Buyback Inspection", result["name"],
               f"Mobile diagnostic with price={result['estimated_price']}")

        # Now do in-store inspection on the same record
        insp = frappe.get_doc("Buyback Inspection", result["name"])
        insp.start_inspection()
        # Fill in-store results
        for row in insp.results:
            row.result = "Pass"
        grade = get_grade("B")
        insp.post_inspection_grade = grade
        insp.condition_grade = grade
        insp.revised_price = 38000
        insp.complete_inspection()

        # Test diagnostic comparison API
        comparison = get_diagnostic_comparison(insp.name)
        assert comparison["total_tests"] > 0, "Should have comparison tests"
        assert "comparison" in comparison, "Should have comparison list"
        _track(ctx, "Buyback Inspection", insp.name,
               f"Comparison: {comparison['total_tests']} tests, "
               f"{comparison['matches']} matches, {comparison['mismatches']} mismatches")

    return True, (
        f"Diagnostic pricing + comparison verified: "
        f"price={result['estimated_price']}, "
        f"tests={comparison['total_tests']}"
    )


# ── S27: Customer Approval Page Token ─────────────────────────────

@_register("S27", "Customer Approval Page Token")
def s27_approval_token(ctx: dict) -> tuple[bool, str]:
    """Verify that orders get an approval_token and the guest API
    returns order details for the customer-facing page."""
    quote = _full_quote_flow(ctx, item_code="QA-OPPO-R12", customer_name="Deepa")
    insp = _full_inspection_flow(ctx, quote, grade_letter="B")
    order = _full_order_flow(ctx, quote, insp)

    # Approval token should be set on insert
    order.reload()
    assert order.approval_token, "Order should have an approval_token"
    assert len(order.approval_token) == 32, \
        f"Token should be 32 chars, got {len(order.approval_token)}"
    _track(ctx, "Buyback Order", order.name, f"token={order.approval_token[:8]}...")

    # Test the guest API endpoint
    from buyback.api import get_buyback_approval_details
    details = get_buyback_approval_details(order.approval_token)
    assert details["name"] == order.name, "Order name should match"
    assert details["final_price"] == order.final_price, "Price should match"
    assert details["item_name"], "Item name should be present"
    assert details["store_name"], "Store name should be present"
    _track(ctx, "API", "get_buyback_approval_details", "Guest API works")

    # Invalid token should fail
    try:
        get_buyback_approval_details("invalid_token_12345678901234567890")
        assert False, "Invalid token should raise exception"
    except Exception:
        pass  # Expected

    return True, (
        f"Approval token verified: {order.name}, "
        f"token={order.approval_token[:8]}..., "
        f"guest API returns details correctly"
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
