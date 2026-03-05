"""
Buyback Pricing Engine
======================
Central pricing logic for calculating buyback amounts.

Flow:
1. Look up base price from Buyback Price Master (grade × warranty matrix)
2. Apply question-based deductions from customer responses
3. Apply Buyback Pricing Rules (flat, %, slab)
4. Round per Buyback Settings
5. Return breakdown: base_price, deductions[], final_price
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate


def calculate_estimated_price(
    item_code: str,
    grade: str,
    warranty_status: str = None,
    device_age_months: int = None,
    responses: list = None,
    brand: str = None,
    item_group: str = None,
):
    """
    Calculate the estimated buyback price for a device.

    Args:
        item_code: Item code (links to Buyback Price Master)
        grade: Grade Master name (e.g. "GRD-00001" or "A")
        warranty_status: "In Warranty" or "Out of Warranty"
        device_age_months: Age of device in months
        responses: List of dicts [{"question_code": "...", "answer_value": "..."}]
        brand: Brand name (for pricing rule matching)
        item_group: Item Group name (for pricing rule matching)

    Returns:
        dict: {
            "base_price": float,
            "deductions": [{"label": str, "amount": float, "type": str}],
            "total_deductions": float,
            "estimated_price": float,
        }
    """
    result = {
        "base_price": 0,
        "deductions": [],
        "total_deductions": 0,
        "estimated_price": 0,
    }

    # Step 1: Look up base price from BPM
    base_price = _get_base_price(item_code, grade, warranty_status, device_age_months)
    result["base_price"] = base_price

    if not base_price:
        return result

    running_price = base_price

    # Step 2: Apply question-based deductions
    if responses:
        for resp in responses:
            deduction = _get_question_deduction(resp, base_price)
            if deduction:
                result["deductions"].append(deduction)
                running_price -= deduction["amount"]

    # Step 3: Apply pricing rules
    rule_deductions = _apply_pricing_rules(
        base_price=base_price,
        brand=brand,
        item_group=item_group,
        grade=grade,
        warranty_status=warranty_status,
        device_age_months=device_age_months,
    )
    result["deductions"].extend(rule_deductions)

    # Step 4: Calculate totals
    result["total_deductions"] = sum(d["amount"] for d in result["deductions"])
    estimated = max(0, base_price - result["total_deductions"])

    # Step 5: Round
    estimated = _round_price(estimated)
    result["estimated_price"] = estimated

    return result


def calculate_final_price(
    buyback_quote_name: str,
    condition_grade: str = None,
    override_amount: float = None,
    override_reason: str = None,
):
    """
    Calculate final price after physical inspection.
    May differ from estimated if grade changed or override applied.

    Args:
        buyback_quote_name: Name of the Buyback Quote
        condition_grade: Actual grade from inspection (may differ from quote)
        override_amount: Manual price override
        override_reason: Reason for override

    Returns:
        dict: {
            "original_estimated": float,
            "recalculated_price": float,
            "final_price": float,
            "price_changed": bool,
            "change_reason": str,
        }
    """
    quote = frappe.get_doc("Buyback Quote", buyback_quote_name)

    result = {
        "original_estimated": quote.estimated_price,
        "recalculated_price": quote.estimated_price,
        "final_price": quote.estimated_price,
        "price_changed": False,
        "change_reason": None,
    }

    # If grade changed, recalculate
    effective_grade = condition_grade or (quote.get("condition_grade") if hasattr(quote, "condition_grade") else None)
    if effective_grade:
        recalc = calculate_estimated_price(
            item_code=quote.item,
            grade=effective_grade,
            warranty_status=quote.warranty_status,
            device_age_months=quote.device_age_months,
            responses=[
                {"question_code": r.question_code, "answer_value": r.answer_value}
                for r in (quote.responses or [])
            ],
            brand=quote.brand,
            item_group=quote.item_group,
        )
        result["recalculated_price"] = recalc["estimated_price"]
        result["final_price"] = recalc["estimated_price"]
        if recalc["estimated_price"] != quote.estimated_price:
            result["price_changed"] = True
            result["change_reason"] = f"Grade changed to {effective_grade}"

    # Manual override takes precedence
    if override_amount is not None:
        result["final_price"] = flt(override_amount)
        result["price_changed"] = True
        result["change_reason"] = override_reason or "Manual price override"

    return result


def get_applicable_rules(brand=None, item_group=None, grade=None,
                          warranty_status=None, device_age_months=None):
    """
    Get all applicable Buyback Pricing Rules for given conditions.

    Returns:
        list[dict]: Matching rules sorted by priority (highest first)
    """
    filters = {"disabled": 0}
    today = nowdate()

    rules = frappe.get_all(
        "Buyback Pricing Rule",
        filters=filters,
        fields=["name", "rule_name", "priority", "rule_type",
                "flat_deduction", "percent_deduction",
                "applies_to_brand", "applies_to_category", "applies_to_grade",
                "warranty_status", "min_age_months", "max_age_months",
                "valid_from", "valid_to"],
        order_by="priority desc",
    )

    applicable = []
    for rule in rules:
        # Check validity
        if rule.valid_from and getdate(rule.valid_from) > getdate(today):
            continue
        if rule.valid_to and getdate(rule.valid_to) < getdate(today):
            continue

        # Check conditions
        if rule.applies_to_brand and rule.applies_to_brand != brand:
            continue
        if rule.applies_to_category and rule.applies_to_category != item_group:
            continue
        if rule.applies_to_grade and rule.applies_to_grade != grade:
            continue
        if rule.warranty_status and rule.warranty_status != warranty_status:
            continue

        # Check age
        if device_age_months is not None:
            if rule.min_age_months and device_age_months < rule.min_age_months:
                continue
            if rule.max_age_months and device_age_months > rule.max_age_months:
                continue

        applicable.append(rule)

    return applicable


def validate_price_override(original_price, override_price, user=None):
    """
    Validate if a price override is within acceptable limits.

    Returns:
        dict: {"allowed": bool, "requires_approval": bool, "message": str}
    """
    if not override_price or override_price <= 0:
        return {"allowed": False, "requires_approval": False,
                "message": _("Override price must be positive.")}

    settings = frappe.get_cached_doc("Buyback Settings")
    max_amount = flt(settings.max_buyback_amount) or 200000
    min_amount = flt(settings.min_buyback_amount) or 100

    if override_price > max_amount:
        return {"allowed": False, "requires_approval": False,
                "message": _("Price exceeds maximum buyback amount of {0}").format(max_amount)}

    if override_price < min_amount:
        return {"allowed": False, "requires_approval": False,
                "message": _("Price below minimum buyback amount of {0}").format(min_amount)}

    approval_threshold = flt(settings.require_manager_approval_above) or 50000
    requires_approval = override_price > approval_threshold

    return {
        "allowed": True,
        "requires_approval": requires_approval,
        "message": _("Price override requires manager approval.") if requires_approval else _("OK"),
    }


# ── Internal Helpers ──────────────────────────────────────────────

def _get_base_price(item_code, grade, warranty_status=None, device_age_months=None):
    """Look up price from Buyback Price Master grade×warranty matrix."""
    bpm = frappe.db.get_value(
        "Buyback Price Master",
        {"item_code": item_code},
        ["name", "current_market_price",
         "a_grade_iw_0_3", "b_grade_iw_0_3", "c_grade_iw_0_3",
         "a_grade_iw_0_6", "b_grade_iw_0_6", "c_grade_iw_0_6", "d_grade_iw_0_6",
         "a_grade_iw_6_11", "b_grade_iw_6_11", "c_grade_iw_6_11", "d_grade_iw_6_11",
         "a_grade_oow_11", "b_grade_oow_11", "c_grade_oow_11", "d_grade_oow_11"],
        as_dict=True,
    )

    if not bpm:
        return 0

    # Resolve grade letter
    grade_letter = _resolve_grade_letter(grade)
    if not grade_letter:
        return flt(bpm.get("current_market_price"))

    # Determine warranty/age bucket
    age = device_age_months or 0
    is_iw = warranty_status == "In Warranty"

    if is_iw and age <= 3:
        field = f"{grade_letter}_grade_iw_0_3"
    elif is_iw and age <= 6:
        field = f"{grade_letter}_grade_iw_0_6"
    elif is_iw and age <= 11:
        field = f"{grade_letter}_grade_iw_6_11"
    else:
        field = f"{grade_letter}_grade_oow_11"

    return flt(bpm.get(field)) or flt(bpm.get("current_market_price"))


def _resolve_grade_letter(grade):
    """Convert grade name/ID to letter (a, b, c, d)."""
    if not grade:
        return None

    # Might be passed as GRD-00001 or as grade name like "A"
    grade_name = grade
    if grade.startswith("GRD-"):
        grade_name = frappe.db.get_value("Grade Master", grade, "grade_name") or grade

    letter = grade_name.strip().lower()
    if letter in ("a", "b", "c", "d"):
        return letter
    return None


def _get_question_deduction(response, base_price):
    """Calculate deduction from a single question response."""
    question_code = response.get("question_code")
    answer_value = response.get("answer_value")

    if not question_code or not answer_value:
        return None

    # Find the question and its option
    question = frappe.db.get_value(
        "Buyback Question Bank",
        {"question_code": question_code, "disabled": 0},
        "name",
    )
    if not question:
        return None

    # Find matching option
    option = frappe.db.get_value(
        "Buyback Question Option",
        {"parent": question, "option_value": answer_value},
        ["option_label", "price_impact_percent"],
        as_dict=True,
    )

    if not option or not option.price_impact_percent:
        return None

    deduction_amount = abs(base_price * flt(option.price_impact_percent) / 100)

    return {
        "label": f"{question_code}: {option.option_label}",
        "amount": deduction_amount,
        "type": "question",
        "percent": abs(option.price_impact_percent),
    }


def _apply_pricing_rules(base_price, brand=None, item_group=None,
                          grade=None, warranty_status=None, device_age_months=None):
    """Apply all matching pricing rules and return deductions list."""
    rules = get_applicable_rules(
        brand=brand,
        item_group=item_group,
        grade=grade,
        warranty_status=warranty_status,
        device_age_months=device_age_months,
    )

    deductions = []
    for rule in rules:
        rule_doc = frappe.get_doc("Buyback Pricing Rule", rule.name)
        deduction_amount = rule_doc.calculate_deduction(base_price)

        if deduction_amount > 0:
            deductions.append({
                "label": rule.rule_name,
                "amount": deduction_amount,
                "type": "rule",
                "rule": rule.name,
            })

    return deductions


def _round_price(price):
    """Round price per Buyback Settings."""
    try:
        rounding = frappe.db.get_single_value("Buyback Settings", "price_rounding")
    except frappe.DoesNotExistError:
        rounding = "Round to nearest 10"

    if rounding == "Round to nearest 10":
        return round(price / 10) * 10
    elif rounding == "Round to nearest 50":
        return round(price / 50) * 50
    elif rounding == "Round to nearest 100":
        return round(price / 100) * 100
    return price
