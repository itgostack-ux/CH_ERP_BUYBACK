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
        "approval_token": doc.approval_token,
        "approval_url": f"/buyback-approval?token={doc.approval_token}",
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
        store: Optional store code (CH Store) where device will be physically brought
        imei_serial: Device IMEI or serial number
        brand: Device brand
        item_group: Device category
        external_diagnostic_id: Reference ID from the mobile diagnostic app

    Returns:
        dict with inspection name, inspection_id, status
    """
    frappe.has_permission("Buyback Inspection", ptype="create", throw=True)

    diag_list = json.loads(diagnostic_results) if isinstance(diagnostic_results, str) else diagnostic_results

    # Look up customer by mobile number
    customer = frappe.db.get_value("Customer", {"mobile_no": mobile_no}, "name")
    customer_name = None
    if customer:
        customer_name = frappe.db.get_value("Customer", customer, "customer_name")

    # Resolve store
    store_name = None
    company = None
    if store:
        store_name = store
        company = frappe.db.get_value("CH Store", store, "company")

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
        # No buyback_quote — this is a mobile-first flow
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
    except Exception:
        frappe.log_error(
            title=f"Mobile diagnostic pricing failed for {doc.name}",
            message=frappe.get_traceback(),
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
    return frappe.get_all(
        "Buyback Inspection",
        filters={"mobile_no": mobile_no},
        fields=[
            "name", "inspection_id", "customer", "customer_name",
            "item", "item_name", "status", "diagnostic_source",
            "mobile_diagnostic_id", "creation",
        ],
        order_by="creation desc",
    )


@frappe.whitelist()
def verify_kyc(order_name: str) -> dict:
    """Verify KYC documents for a buyback order."""
    doc = frappe.get_doc("Buyback Order", order_name)
    doc.check_permission("write")
    doc.verify_kyc()
    return {
        "name": doc.name,
        "kyc_verified": doc.kyc_verified,
        "kyc_verified_by": doc.kyc_verified_by,
    }


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

def _map_diagnostic_to_responses(diag_list: list[dict]) -> list[dict]:
    """Map mobile diagnostic results to question-style responses for pricing.

    Mobile diagnostics have: {test, code, result, status}
    Pricing engine expects: {question_code, answer_value}

    We attempt to match diagnostic codes to question bank codes.
    Unmatched diagnostics are skipped (pricing engine ignores unknown codes).
    """
    responses = []
    for d in diag_list:
        code = d.get("code", "")
        status = (d.get("status") or "N/A").lower()
        # Map Pass/Fail to yes/no answer values
        answer = "yes" if status == "pass" else "no" if status == "fail" else status
        # Only include if a matching question code exists
        if code and frappe.db.exists("Buyback Question Bank", {"question_code": code}):
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

    return frappe.get_all(
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
        limit_page_length=int(limit),
    )


# ── Lookup by Phone (Quotes + Orders) ───────────────────────────


@frappe.whitelist()
def get_quotes_by_phone(mobile_no: str) -> list[dict]:
    """Look up all Buyback Quotes for a given mobile number.

    Used by store agents to find pending quotes when customer walks in.
    """
    return frappe.get_all(
        "Buyback Quote",
        filters={"mobile_no": mobile_no},
        fields=[
            "name", "quote_id", "customer", "customer_name",
            "item", "item_name", "imei_serial", "quoted_price",
            "estimated_price", "status", "valid_until", "creation",
        ],
        order_by="creation desc",
    )


@frappe.whitelist()
def get_orders_by_phone(mobile_no: str) -> list[dict]:
    """Look up all Buyback Orders for a given mobile number.

    Used by store agents to find existing orders for a customer.
    """
    return frappe.get_all(
        "Buyback Order",
        filters={"mobile_no": mobile_no},
        fields=[
            "name", "order_id", "customer", "customer_name",
            "item", "item_name", "imei_serial", "final_price",
            "condition_grade", "status", "payment_status",
            "workflow_state", "creation",
        ],
        order_by="creation desc",
    )


# ── IMEI History API ────────────────────────────────────────────


@frappe.whitelist()
def get_imei_history(imei: str) -> dict:
    """Get consolidated buyback history for an IMEI/Serial No.

    Queries across Serial No, Quotes, Inspections, Orders, Exchanges
    and returns a unified timeline view. Reuses ERPNext's Serial No
    DocType — no separate IMEI History DocType needed.
    """
    from buyback.serial_no_utils import get_imei_history as _get_history
    return _get_history(imei)


# ── Customer Approval Page Data ─────────────────────────────────


@frappe.whitelist(allow_guest=True)
def get_buyback_approval_details(token: str) -> dict:
    """Get buyback order details for the customer-facing approval page.

    The token is a hash stored on the order — no login required.
    Customer sees: item details, price, store, photos, and can
    trigger OTP verification from the approval page.
    """
    order_name = frappe.db.get_value(
        "Buyback Order", {"approval_token": token, "docstatus": ["!=", 2]}, "name"
    )
    if not order_name:
        frappe.throw(_("Invalid or expired approval link."), exc=frappe.DoesNotExistError)

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
            "CH Store", order.store, "store_name"
        ) if order.store else "",
        "status": order.status,
        "device_photo_front": order.device_photo_front,
        "device_photo_back": order.device_photo_back,
        "otp_verified": order.otp_verified,
        "warranty_status": order.warranty_status,
    }


# ── Diagnostic Comparison ───────────────────────────────────────


@frappe.whitelist()
def get_diagnostic_comparison(inspection_name: str) -> dict:
    """Get a normalized side-by-side comparison of mobile diagnostic
    answers vs in-store inspection results.

    Returns a list of items, each with:
      test_name, code, mobile_result, mobile_status, store_result, match
    """
    doc = frappe.get_doc("Buyback Inspection", inspection_name)
    doc.check_permission("read")

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