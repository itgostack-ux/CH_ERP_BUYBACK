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

import json

import frappe
from frappe import _
from frappe.utils import flt

from buyback.exceptions import (
    BuybackStatusError,
    BuybackValidationError,
)


# ── Legacy Endpoints (kept for backward compatibility) ───────────


@frappe.whitelist()
def get_buyback(id: int | str) -> dict:
    """Return a Buyback Request by its buybackid. Legacy endpoint."""
    name = frappe.db.get_value("Buyback Request", {"buybackid": id}, "name")
    if not name:
        frappe.throw(
            _("Buyback Request {0} not found").format(id),
            exc=frappe.DoesNotExistError,
        )

    doc = frappe.get_doc("Buyback Request", name)
    doc.check_permission("read")

    return {
        "name": doc.name,
        "buybackid": doc.buybackid,
        "customer_name": doc.customer_name,
        "mobile_no": doc.mobile_no,
        "item_code": doc.get("item_code"),
        "item_full_name": doc.get("item_full_name"),
        "grade": doc.grade,
        "usage_key": doc.usage_key,
        "buyback_price": doc.buyback_price,
        "final_buyback_amount": doc.get("final_buyback_amount"),
        "status": doc.status,
        "deal_status": doc.deal_status,
        "mode": doc.get("mode"),
    }


@frappe.whitelist()
def confirm_deal(name: str) -> dict:
    """Legacy endpoint to confirm a deal."""
    doc = frappe.get_doc("Buyback Request", name)
    doc.check_permission("write")
    if doc.status != "Open Request":
        return {"status": "already_processed"}
    doc.status = "Customer Approved"
    doc.save()
    return {"status": "success"}


# ── Step 1: Get Estimate ─────────────────────────────────────────


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
        device_age_months=int(device_age_months) if device_age_months else None,
        responses=resp_list,
        brand=brand,
        item_group=item_group,
    )


# ── Step 2: Create Quote ─────────────────────────────────────────


@frappe.whitelist()
def create_quote(
    customer: str,
    mobile_no: str,
    store: str,
    item: str,
    brand: str | None = None,
    item_group: str | None = None,
    imei_serial: str | None = None,
    warranty_status: str | None = None,
    device_age_months: int | str | None = None,
    responses: str | None = None,
) -> dict:
    """
    Create a Buyback Quote with auto-pricing.

    Returns:
        dict: {name, quote_id, estimated_price, quoted_price, valid_until}
    """
    from buyback.buyback.pricing.engine import calculate_estimated_price

    frappe.has_permission("Buyback Quote", ptype="create", throw=True)

    resp_list = json.loads(responses) if isinstance(responses, str) else (responses or [])

    # Auto-calculate price
    pricing = calculate_estimated_price(
        item_code=item,
        grade=None,  # grade determined during inspection
        warranty_status=warranty_status,
        device_age_months=int(device_age_months) if device_age_months else None,
        responses=resp_list,
        brand=brand,
        item_group=item_group,
    )

    doc = frappe.get_doc(
        {
            "doctype": "Buyback Quote",
            "customer": customer,
            "mobile_no": mobile_no,
            "store": store,
            "item": item,
            "brand": brand,
            "item_group": item_group,
            "imei_serial": imei_serial,
            "warranty_status": warranty_status,
            "device_age_months": int(device_age_months) if device_age_months else None,
            "base_price": pricing["base_price"],
            "total_deductions": pricing["total_deductions"],
            "estimated_price": pricing["estimated_price"],
            "quoted_price": pricing["estimated_price"],
            "responses": [
                {
                    "question": _get_question_name(r.get("question_code")),
                    "question_code": r.get("question_code"),
                    "answer_value": r.get("answer_value"),
                    "answer_label": r.get("answer_label", ""),
                    "price_impact_percent": r.get("price_impact_percent", 0),
                }
                for r in resp_list
            ],
        }
    )
    doc.insert()
    doc.mark_quoted()

    return {
        "name": doc.name,
        "quote_id": doc.quote_id,
        "estimated_price": doc.estimated_price,
        "quoted_price": doc.quoted_price,
        "valid_until": str(doc.valid_until),
        "status": doc.status,
    }


# ── Step 3: Accept Quote ─────────────────────────────────────────


@frappe.whitelist()
def accept_quote(quote_name: str) -> dict:
    """Customer accepts a buyback quote."""
    doc = frappe.get_doc("Buyback Quote", quote_name)
    doc.check_permission("write")
    doc.mark_accepted()
    return {"name": doc.name, "status": doc.status}


# ── Step 4: Create / Manage Inspection ───────────────────────────


@frappe.whitelist()
def create_inspection(
    quote_name: str,
    checklist_template: str | None = None,
) -> dict:
    """Create a Buyback Inspection from an accepted quote."""
    quote = frappe.get_doc("Buyback Quote", quote_name)
    quote.check_permission("read")

    if quote.status != "Accepted":
        frappe.throw(
            _("Quote must be in Accepted status."),
            exc=BuybackStatusError,
        )

    frappe.has_permission("Buyback Inspection", ptype="create", throw=True)

    doc = frappe.get_doc(
        {
            "doctype": "Buyback Inspection",
            "buyback_quote": quote_name,
            "checklist_template": checklist_template,
            "pre_inspection_grade": None,
            "quoted_price": quote.quoted_price,
        }
    )
    doc.insert()

    if checklist_template:
        doc.populate_checklist()
        doc.save()

    return {
        "name": doc.name,
        "inspection_id": doc.inspection_id,
        "status": doc.status,
    }


@frappe.whitelist()
def start_inspection(inspection_name: str) -> dict:
    """Start an inspection."""
    doc = frappe.get_doc("Buyback Inspection", inspection_name)
    doc.check_permission("write")
    doc.start_inspection()
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
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


@frappe.whitelist()
def create_order(
    customer: str,
    mobile_no: str,
    store: str,
    item: str,
    condition_grade: str,
    final_price: float | str,
    buyback_quote: str | None = None,
    buyback_inspection: str | None = None,
    imei_serial: str | None = None,
    warranty_status: str | None = None,
    brand: str | None = None,
) -> dict:
    """Create a Buyback Order (submittable)."""
    frappe.has_permission("Buyback Order", ptype="create", throw=True)

    doc = frappe.get_doc(
        {
            "doctype": "Buyback Order",
            "customer": customer,
            "mobile_no": mobile_no,
            "store": store,
            "item": item,
            "condition_grade": condition_grade,
            "final_price": flt(final_price),
            "buyback_quote": buyback_quote,
            "buyback_inspection": buyback_inspection,
            "imei_serial": imei_serial,
            "warranty_status": warranty_status,
            "brand": brand,
        }
    )
    doc.insert()
    doc.submit()

    return {
        "name": doc.name,
        "order_id": doc.order_id,
        "status": doc.status,
        "requires_approval": doc.requires_approval,
        "final_price": doc.final_price,
    }


# ── Step 6: Approve / Reject Order ───────────────────────────────


@frappe.whitelist()
def approve_order(order_name: str, remarks: str | None = None) -> dict:
    """Manager approves a buyback order."""
    doc = frappe.get_doc("Buyback Order", order_name)
    doc.check_permission("write")
    doc.approve(remarks)
    return {"name": doc.name, "status": doc.status, "approved_by": doc.approved_by}


@frappe.whitelist()
def reject_order(order_name: str, remarks: str | None = None) -> dict:
    """Manager rejects a buyback order."""
    doc = frappe.get_doc("Buyback Order", order_name)
    doc.check_permission("write")
    doc.reject(remarks)
    return {"name": doc.name, "status": doc.status}


# ── Step 7: OTP Verification ─────────────────────────────────────


@frappe.whitelist()
def send_otp(order_name: str) -> dict:
    """Send OTP for buyback order confirmation."""
    doc = frappe.get_doc("Buyback Order", order_name)
    doc.check_permission("write")
    doc.send_otp()
    return {"status": "sent", "message": _("OTP sent to {0}").format(doc.mobile_no)}


@frappe.whitelist()
def verify_otp(order_name: str, otp_code: str) -> dict:
    """Verify customer OTP for a buyback order."""
    doc = frappe.get_doc("Buyback Order", order_name)
    doc.check_permission("write")
    return doc.verify_otp(otp_code)


# ── Step 8: Payment ──────────────────────────────────────────────


@frappe.whitelist()
def record_payment(
    order_name: str,
    payment_method: str,
    amount: float | str,
    transaction_reference: str | None = None,
) -> dict:
    """Record a payment against a buyback order."""
    doc = frappe.get_doc("Buyback Order", order_name)
    doc.check_permission("write")

    doc.append(
        "payments",
        {
            "payment_method": payment_method,
            "amount": flt(amount),
            "transaction_reference": transaction_reference,
            "payment_date": frappe.utils.now_datetime(),
        },
    )
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


@frappe.whitelist()
def close_order(order_name: str) -> dict:
    """Close a fully paid buyback order."""
    doc = frappe.get_doc("Buyback Order", order_name)
    doc.check_permission("write")
    doc.close()
    return {"name": doc.name, "status": doc.status}


# ── Exchange Endpoints ────────────────────────────────────────────


@frappe.whitelist()
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
    """Create a Buyback Exchange Order."""
    frappe.has_permission("Buyback Exchange Order", ptype="create", throw=True)

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


@frappe.whitelist()
def advance_exchange(exchange_name: str, action: str) -> dict:
    """Advance an exchange order through its workflow.

    Args:
        action: one of 'deliver', 'receive', 'inspect', 'settle', 'close'
    """
    doc = frappe.get_doc("Buyback Exchange Order", exchange_name)
    doc.check_permission("write")

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


@frappe.whitelist()
def get_questions(category: str | None = None) -> list[dict]:
    """Get active questions for a category (or all)."""
    filters: dict = {"disabled": 0}
    if category:
        filters["applies_to_category"] = ["in", [category, "", None]]

    questions = frappe.get_all(
        "Buyback Question Bank",
        filters=filters,
        fields=[
            "name", "question_id", "question_text", "question_code",
            "question_type", "display_order", "is_mandatory",
        ],
        order_by="display_order asc",
    )

    # Attach options
    for q in questions:
        q["options"] = frappe.get_all(
            "Buyback Question Option",
            filters={"parent": q["name"]},
            fields=["option_label", "option_value", "price_impact_percent", "is_default"],
            order_by="idx asc",
        )

    return questions


@frappe.whitelist()
def get_grades() -> list[dict]:
    """Get all active grades."""
    return frappe.get_all(
        "Grade Master",
        filters={"disabled": 0},
        fields=["name", "grade_id", "grade_name", "description", "display_order"],
        order_by="display_order asc",
    )


@frappe.whitelist()
def get_stores(
    company: str | None = None,
    buyback_enabled: int | str | None = None,
) -> list[dict]:
    """Get active stores, optionally filtered."""
    filters: dict = {"disabled": 0}
    if company:
        filters["company"] = company
    if buyback_enabled:
        filters["is_buyback_enabled"] = 1

    return frappe.get_all(
        "CH Store",
        filters=filters,
        fields=[
            "name", "store_id", "store_code", "store_name",
            "company", "city", "state", "pincode",
        ],
        order_by="store_name asc",
    )


@frappe.whitelist()
def get_payment_methods() -> list[dict]:
    """Get active payment methods."""
    return frappe.get_all(
        "CH Payment Method",
        filters={"disabled": 0},
        fields=[
            "name", "payment_method_id", "method_name", "method_type",
            "requires_bank_details", "requires_upi_id",
            "requires_transaction_proof",
        ],
        order_by="method_name asc",
    )


# ── Helpers ───────────────────────────────────────────────────────


def _get_question_name(question_code: str | None) -> str | None:
    """Look up Buyback Question Bank name from question_code."""
    if not question_code:
        return None
    return frappe.db.get_value(
        "Buyback Question Bank",
        {"question_code": question_code},
        "name",
    )
