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
    diagnostic_tests: list = None,
    brand: str = None,
    item_group: str = None,
    is_phone_dead: bool = False,         
):
    """Calculate the estimated buyback price for a device."""
    result = {
        "base_price": 0,
        "deductions": [],
        "total_deductions": 0,
        "estimated_price": 0,
        "grade_letter": "A",
    }

    # PHONE DEAD OVERRIDE — return Phone Dead price directly
    if is_phone_dead:
        dead_price = _get_phone_dead_price(item_code, warranty_status, device_age_months)
        
        result["base_price"] = dead_price
        result["estimated_price"] = _round_price(dead_price)
        result["grade_letter"] = "F" 
        result["is_phone_dead"] = True
        return result

    resolved_age = _resolve_age_months(device_age_months)
    base_price = _get_base_price(item_code, grade, warranty_status, device_age_months)
    result["base_price"] = base_price

    if not base_price:
        return result

    # Apply diagnostic + question deductions
    if diagnostic_tests:
        for dt in diagnostic_tests:
            deduction = _get_diagnostic_deduction(dt, base_price)
            if deduction:
                result["deductions"].append(deduction)

    if responses:
        for resp in responses:
            deduction = _get_question_deduction(resp, base_price)
            if deduction:
                result["deductions"].append(deduction)

    # Apply pricing rules
    rule_deductions = _apply_pricing_rules(
        base_price=base_price,
        brand=brand,
        item_group=item_group,
        grade=grade,
        warranty_status=warranty_status,
        device_age_months=resolved_age,
    )
    result["deductions"].extend(rule_deductions)

    result["total_deductions"] = sum(d["amount"] for d in result["deductions"])
    estimated = base_price - result["total_deductions"]

    # NEW FLOOR LOGIC:
    # If estimated < min grade price → use Scrap Price (never below scrap)
    min_grade_price = _get_min_grade_price(item_code, warranty_status, device_age_months)
    
    if estimated < min_grade_price:
        # Deductions dropped price below minimum grade → use Scrap Price
        scrap_price = _get_scrap_price(item_code, warranty_status, device_age_months)
        estimated = scrap_price
        result["is_scrap"] = True

    estimated = _round_price(estimated)
    result["estimated_price"] = estimated

    # Determine grade
    if result.get("is_scrap"):
        result["grade_letter"] = "E"                         # ← NEW: Always E for scrap
    else:
        result["grade_letter"] = _determine_grade_from_price(
            item_code=item_code,
            final_price=estimated,
            warranty_status=warranty_status,
            device_age_months=device_age_months,
        )

    return result


def calculate_final_price(
    assessment_name: str,
    condition_grade: str = None,
    override_amount: float = None,
    override_reason: str = None,
):
    """
    Calculate final price after physical inspection.
    May differ from estimated if grade changed or override applied.

    Args:
        assessment_name: Name of the Buyback Assessment
        condition_grade: Actual grade from inspection (may differ from assessment)
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
    assessment = frappe.get_doc("Buyback Assessment", assessment_name)
    original_price = assessment.quoted_price or assessment.estimated_price

    result = {
        "original_estimated": original_price,
        "recalculated_price": original_price,
        "final_price": original_price,
        "price_changed": False,
        "change_reason": None,
    }

    # If grade changed, recalculate
    effective_grade = condition_grade or assessment.estimated_grade
    if effective_grade:
        recalc = calculate_estimated_price(
            item_code=assessment.item,
            grade=effective_grade,
            warranty_status=assessment.warranty_status,
            device_age_months=assessment.device_age_months,
            responses=[
                {"question_code": r.question_code, "answer_value": r.answer_value}
                for r in (assessment.responses or [])
            ],
            diagnostic_tests=[
                {"test_code": d.test_code, "result": d.result}
                for d in (assessment.diagnostic_tests or [])
            ],
            brand=assessment.brand,
            item_group=assessment.item_group,
        )
        result["recalculated_price"] = recalc["estimated_price"]
        result["final_price"] = recalc["estimated_price"]
        if recalc["estimated_price"] != original_price:
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
    """Look up base price from Ready Reckoner.
    
    IMPORTANT: For estimation, always use Grade A as the starting base.
    Deductions are then applied to find the true value.
    Final grade is determined from the resulting price.
    """
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

    age = _resolve_age_months(device_age_months)
    is_iw = warranty_status == "In Warranty"

    # Determine bucket
    if is_iw and age <= 3:
        bucket = "iw_0_3"
    elif is_iw and age <= 6:
        bucket = "iw_0_6"
    elif is_iw and age <= 11:
        bucket = "iw_6_11"
    else:
        bucket = "oow_11"

    field = f"a_grade_{bucket}"
    price = flt(bpm.get(field))

    if not price:
        for fallback in ["b", "c", "d"]:
            fb_price = flt(bpm.get(f"{fallback}_grade_{bucket}"))
            if fb_price:
                price = fb_price
                break

    return price

def _resolve_age_months(device_age_months):
    """Convert age bracket label to a representative numeric value.

    Accepts either:
      - Select labels: '0-3 Months', '4-6 Months', '7-11 Months', '12+ Months'
      - Raw int / string int (for backward compatibility / API)
    Returns int used by the bucket logic (0-3, 4-6, 7-11, 12+).
    """
    if not device_age_months:
        return 0

    mapping = {
        "0-3 Months": 2,
        "4-6 Months": 5,
        "7-11 Months": 9,
        "12+ Months": 14,
    }
    val = str(device_age_months).strip()
    if val in mapping:
        return mapping[val]

    # Backward compat: raw int
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


# POS condition-check keys → Buyback Question Bank question_code.
# The POS quick-grading UI sends short keys (screen/camera/…); the question
# bank is seeded with diag-*/q-* codes. Without this mapping every failed
# POS check silently deducted 0%.
_DIAG_CODE_ALIASES = {
    "screen": "diag-screen",
    "camera": "diag-camera",
    "speaker_mic": "diag-speaker",
    "speaker": "diag-speaker",
    "charging": "diag-charge",
    "charge": "diag-charge",
    "battery": "diag-battery",
    "body": "q-cosmetic",
    "cosmetic": "q-cosmetic",
}


def _resolve_diag_question(test_code):
    """Return the enabled Buyback Question Bank name for a diagnostic code,
    trying the exact code, the known POS alias, then a diag- prefix."""
    key = (test_code or "").strip()
    candidates = [key]
    alias = _DIAG_CODE_ALIASES.get(key.lower())
    if alias:
        candidates.append(alias)
    else:
        candidates.append(f"diag-{key.lower()}")
    for code in candidates:
        name = frappe.db.get_value(
            "Buyback Question Bank", {"question_code": code, "disabled": 0}, "name"
        )
        if name:
            return name
    return None


def _get_diagnostic_deduction(diagnostic_test, base_price):
    """Calculate deduction from an automated diagnostic test result.    """
    test_code = diagnostic_test.get("test_code")
    result = diagnostic_test.get("result")

    if not test_code or not result:
        return None

    question = _resolve_diag_question(test_code)
    if not question:
        return None

    result_str = str(result).strip().lower()
    if result_str == "yes":
        lookup_value = "Fail"
    elif result_str == "no":
        lookup_value = "Pass"
    else:
        lookup_value = result  # already Pass/Fail/Partial

    option = frappe.db.get_value("Buyback Question Option",{"parent": question, "option_value": lookup_value},
        ["option_label", "price_impact_percent"], as_dict=True, )

    if not option:
        all_opts = frappe.get_all("Buyback Question Option", filters={"parent": question}, 
                                  fields=["option_label", "option_value", "price_impact_percent"] )
        
        for opt in all_opts:
            if (opt.option_value or "").strip().lower() == lookup_value.lower():
                option = opt
                break

    if not option or not option.get("price_impact_percent"):
        return None

    deduction_amount = abs(base_price * flt(option.get("price_impact_percent")) / 100)

    return {
        "label": f"{test_code}: {option.get('option_label') or result}",
        "amount": deduction_amount,
        "type": "diagnostic_test",
        "percent": abs(option.get("price_impact_percent")),
    }

def _get_question_deduction(response, base_price):
    """Calculate deduction from a single question response."""
    question_code = response.get("question_code")
    answer_value = response.get("answer_value")

    if not question_code or not answer_value:
        return None

    question = frappe.db.get_value(
        "Buyback Question Bank",
        {"question_code": question_code, "disabled": 0},
        "name",
    )
    if not question:
        return None

    # Find matching option
    option = frappe.db.get_value("Buyback Question Option", {"parent": question, "option_value": answer_value},
        ["option_label", "price_impact_percent"], as_dict=True,)

    if not option:
        all_opts = frappe.get_all("Buyback Question Option",filters={"parent": question},
                                  fields=["option_label", "option_value", "price_impact_percent"] )
        
        for opt in all_opts:
            if (opt.option_value or "").strip().lower() == str(answer_value).strip().lower():
                option = opt
                break

    if not option or not option.get("price_impact_percent"):
        return None

    deduction_amount = abs(base_price * flt(option.get("price_impact_percent")) / 100)

    return {
        "label": f"{question_code}: {option.get('option_label')}",
        "amount": deduction_amount,
        "type": "question",
        "percent": abs(option.get("price_impact_percent")),
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
    return round(float(price or 0), 2)

def _determine_grade_from_price(item_code, final_price, warranty_status=None, device_age_months=None):
    """Determine grade by finding which price bracket the final price falls into."""
    bpm = frappe.db.get_value(
        "Buyback Price Master",
        {"item_code": item_code},
        ["a_grade_iw_0_3", "b_grade_iw_0_3", "c_grade_iw_0_3",
         "a_grade_iw_0_6", "b_grade_iw_0_6", "c_grade_iw_0_6", "d_grade_iw_0_6",
         "a_grade_iw_6_11", "b_grade_iw_6_11", "c_grade_iw_6_11", "d_grade_iw_6_11",
         "a_grade_oow_11", "b_grade_oow_11", "c_grade_oow_11", "d_grade_oow_11"],
        as_dict=True,
    )

    if not bpm or not final_price:
        return "A"

    age = _resolve_age_months(device_age_months)
    is_iw = warranty_status == "In Warranty"

    if is_iw and age <= 3:
        bucket = "iw_0_3"
        grade_letters = ["a", "b", "c"]
    elif is_iw and age <= 6:
        bucket = "iw_0_6"
        grade_letters = ["a", "b", "c", "d"]
    elif is_iw and age <= 11:
        bucket = "iw_6_11"
        grade_letters = ["a", "b", "c", "d"]
    else:
        bucket = "oow_11"
        grade_letters = ["a", "b", "c", "d"]

    grade_prices = []
    for g in grade_letters:
        p = flt(bpm.get(f"{g}_grade_{bucket}"))
        if p > 0:
            grade_prices.append((g.upper(), p))

    if not grade_prices:
        return "A"

    grade_prices.sort(key=lambda x: x[1], reverse=True)
    final = flt(final_price)

    if final >= grade_prices[0][1]:
        return grade_prices[0][0]
    if final <= grade_prices[-1][1]:
        return grade_prices[-1][0]

    for i in range(len(grade_prices) - 1):
        upper_grade, upper_price = grade_prices[i]
        lower_grade, lower_price = grade_prices[i + 1]
        if lower_price <= final <= upper_price:
            midpoint = (upper_price + lower_price) / 2
            return upper_grade if final >= midpoint else lower_grade

    return grade_prices[-1][0]

def _get_phone_dead_price(item_code, warranty_status, device_age_months):
    """Return Phone Dead price from Ready Reckoner for the current bucket."""
    bpm = frappe.db.get_value(
        "Buyback Price Master",
        {"item_code": item_code},
        [
            "phone_dead_iw_0_3", "phone_dead_iw_0_6",
            "phone_dead_iw_6_11", "phone_dead_oow_11",
        ],
        as_dict=True,
    )
    if not bpm:
        return 0

    age = _resolve_age_months(device_age_months)
    is_iw = warranty_status == "In Warranty"

    if is_iw and age <= 3:
        return flt(bpm.get("phone_dead_iw_0_3"))
    elif is_iw and age <= 6:
        return flt(bpm.get("phone_dead_iw_0_6"))
    elif is_iw and age <= 11:
        return flt(bpm.get("phone_dead_iw_6_11"))
    else:
        return flt(bpm.get("phone_dead_oow_11"))


def _get_scrap_price(item_code, warranty_status, device_age_months):
    """Return Scrap Price from Ready Reckoner for the current bucket."""
    bpm = frappe.db.get_value(
        "Buyback Price Master",
        {"item_code": item_code},
        [
            "scrap_iw_0_3", "scrap_iw_0_6",
            "scrap_iw_6_11", "scrap_oow_11",
        ],
        as_dict=True,
    )
    if not bpm:
        return 0

    age = _resolve_age_months(device_age_months)
    is_iw = warranty_status == "In Warranty"

    if is_iw and age <= 3:
        return flt(bpm.get("scrap_iw_0_3"))
    elif is_iw and age <= 6:
        return flt(bpm.get("scrap_iw_0_6"))
    elif is_iw and age <= 11:
        return flt(bpm.get("scrap_iw_6_11"))
    else:
        return flt(bpm.get("scrap_oow_11"))


def _get_min_grade_price(item_code, warranty_status, device_age_months):
    """Return the minimum grade price for the bucket.
    
    - 0-3 months: C grade (no D)
    - Other buckets: D grade
    """
    bpm = frappe.db.get_value(
        "Buyback Price Master",
        {"item_code": item_code},
        [
            "c_grade_iw_0_3",
            "d_grade_iw_0_6", "d_grade_iw_6_11", "d_grade_oow_11",
        ],
        as_dict=True,
    )
    if not bpm:
        return 0

    age = _resolve_age_months(device_age_months)
    is_iw = warranty_status == "In Warranty"

    if is_iw and age <= 3:
        return flt(bpm.get("c_grade_iw_0_3"))
    elif is_iw and age <= 6:
        return flt(bpm.get("d_grade_iw_0_6"))
    elif is_iw and age <= 11:
        return flt(bpm.get("d_grade_iw_6_11"))
    else:
        return flt(bpm.get("d_grade_oow_11"))
