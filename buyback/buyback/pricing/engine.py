"""
Buyback Pricing Engine
======================
Central pricing logic for calculating buyback amounts.

Flow:
1. Look up base price from Buyback Price Master (grade × warranty matrix)
2. Apply question-based deductions from customer responses
3. Apply Buyback Pricing Rules (flat, %, slab)
4. Clamp the deduction total, then floor at the salvage price
5. Round per Buyback Settings
6. Return breakdown: base_price, deductions[], final_price

The engine is STRICT about its inputs: warranty status and device age select
the price band, so pricing without them is not a lower-confidence estimate,
it is a different number entirely. Callers that cannot supply them must not
call the engine — see BuybackAssessment._calculate_estimate for the pattern.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

# Deductions can never take more than this share of the base price unless the
# Buyback Settings override says otherwise. Without a clamp, a device that
# answers "faulty" to enough questions produces a negative quote which the
# salvage floor then silently rescues — hiding the misconfiguration.
DEFAULT_MAX_TOTAL_DEDUCTION_PERCENT = 100.0


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
    """Calculate the estimated buyback price for a device.

    Raises:
        frappe.ValidationError: if warranty status or device age is missing, or
            if the Buyback Price Master has no price for the resolved band.
    """
    result = {
        "base_price": 0,
        "deductions": [],
        "total_deductions": 0,
        "estimated_price": 0,
        "grade_letter": "A",
    }

    # PHONE DEAD OVERRIDE — return Phone Dead price directly.
    # Age and warranty are deliberately NOT required here: a handset that does
    # not power on is worth its salvage value whatever its age.
    if is_phone_dead:
        dead_price = _get_phone_dead_price(item_code, warranty_status, device_age_months)
        if not dead_price:
            frappe.throw(
                _("No Phone Dead price is configured for {0}. Set it on the "
                  "Buyback Price Master before quoting a dead handset.").format(item_code),
                title=_("Salvage Price Missing"),
            )
        result["base_price"] = dead_price
        result["estimated_price"] = _round_price(dead_price)
        result["grade_letter"] = "F"
        result["is_phone_dead"] = True
        return result

    _require_band_inputs(warranty_status, device_age_months)

    resolved_age = _resolve_age_months(device_age_months)
    base_price = _get_base_price(item_code, grade, warranty_status, device_age_months)
    result["base_price"] = base_price

    if not base_price:
        return result

    # Collect deductions, keyed so the same physical fault cannot be charged
    # twice when it is covered by both an automated test and a customer
    # question (see _deduction_key).
    collected: dict = {}

    for dt in (diagnostic_tests or []):
        _collect_deduction(collected, _get_diagnostic_deduction(dt, base_price))

    for resp in (responses or []):
        _collect_deduction(collected, _get_question_deduction(resp, base_price))

    result["deductions"] = list(collected.values())

    # Pricing rules are keyed by rule name, never by fault, so they bypass the
    # fault de-duplication above by design.
    result["deductions"].extend(_apply_pricing_rules(
        base_price=base_price,
        brand=brand,
        item_group=item_group,
        grade=grade,
        warranty_status=warranty_status,
        device_age_months=resolved_age,
    ))

    raw_total = sum(d["amount"] for d in result["deductions"])
    capped_total = _clamp_deductions(raw_total, base_price)
    if capped_total < raw_total:
        result["deduction_cap_applied"] = True
        result["uncapped_deductions"] = _round_price(raw_total)

    result["total_deductions"] = _round_price(capped_total)
    estimated = base_price - capped_total

    # Floor: if deductions drop the quote below the band's minimum grade price,
    # the device is worth its scrap value rather than a graded price.
    min_grade_price = _get_min_grade_price(item_code, warranty_status, device_age_months)

    if estimated < min_grade_price:
        scrap_price = _get_scrap_price(item_code, warranty_status, device_age_months)
        if not scrap_price:
            frappe.throw(
                _("Deductions took the quote for {0} below the minimum grade price, "
                  "but no Scrap Price is configured on its Buyback Price Master. "
                  "Set a Scrap Price before quoting this device.").format(item_code),
                title=_("Salvage Price Missing"),
            )
        estimated = scrap_price
        result["is_scrap"] = True

    estimated = _round_price(estimated)
    result["estimated_price"] = estimated

    # Determine grade
    if result.get("is_scrap"):
        result["grade_letter"] = "E"
    else:
        result["grade_letter"] = _determine_grade_from_price(
            item_code=item_code,
            final_price=estimated,
            warranty_status=warranty_status,
            device_age_months=device_age_months,
        )

    return result


def _require_band_inputs(warranty_status, device_age_months):
    """Refuse to price without the two inputs that select the price band.

    Treating a missing age as 0 put every under-specified quote in the highest
    In-Warranty band, and a missing warranty status in the lowest — so silence
    here was worth thousands of rupees in either direction.
    """
    missing = []
    if not (warranty_status or "").strip():
        missing.append(_("Warranty Status"))
    if not str(device_age_months or "").strip():
        missing.append(_("Device Age"))
    if missing:
        frappe.throw(
            _("{0} must be set before a buyback price can be calculated — "
              "they select which price band applies.").format(", ".join(missing)),
            title=_("Cannot Price Device"),
        )


def _deduction_key(question_name: str, fault_code: str | None) -> str:
    """Identity a deduction is charged under.

    Prefer the fault code so two DIFFERENT questions covering one physical
    fault collapse to a single charge. Fall back to the question itself, which
    at minimum stops the same question counting twice when it appears in both
    the diagnostic and the response table.
    """
    code = (fault_code or "").strip()
    return f"fault:{code.lower()}" if code else f"question:{question_name}"


def _collect_deduction(collected: dict, deduction: dict | None) -> None:
    """Keep the largest deduction per fault; drop the rest as duplicates."""
    if not deduction:
        return
    key = deduction.pop("_key", None) or f"anon:{len(collected)}"
    existing = collected.get(key)
    if existing is None or deduction["amount"] > existing["amount"]:
        if existing is not None:
            deduction["superseded"] = existing["label"]
        collected[key] = deduction


def _clamp_deductions(total: float, base_price: float) -> float:
    """Cap the deduction total at the configured share of the base price."""
    try:
        configured = frappe.db.get_single_value(
            "Buyback Settings", "max_total_deduction_percent"
        )
    except Exception:
        configured = None
    percent = flt(configured) or DEFAULT_MAX_TOTAL_DEDUCTION_PERCENT
    percent = max(0.0, min(percent, 100.0))
    ceiling = flt(base_price) * percent / 100.0
    return min(flt(total), ceiling)


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
        frappe.throw(
            _("No Buyback Price Master exists for {0}. Pricing must be configured "
              "before this device can be quoted.").format(item_code),
            title=_("Buyback Price Missing"),
        )

    bucket = _resolve_bucket(warranty_status, device_age_months)
    price = flt(bpm.get(f"a_grade_{bucket}"))

    # No silent grade fallback. Substituting the B/C/D cell when the A cell is
    # blank quoted a lower grade's price while still calling it Grade A, and
    # nothing surfaced the substitution. An unpriced band is a configuration
    # gap and must be fixed in the master, not papered over here.
    if not price:
        frappe.throw(
            _("No Grade A price is configured for {0} in the {1} band. "
              "Set it on the Buyback Price Master before quoting this device.").format(
                item_code, _BUCKET_LABELS.get(bucket, bucket)
            ),
            title=_("Buyback Price Missing"),
        )

    return price


# Warranty/age band keys used across Buyback Price Master column names.
_BUCKET_LABELS = {
    "iw_0_3": "In Warranty 0–3 Months",
    "iw_0_6": "In Warranty 4–6 Months",
    "iw_6_11": "In Warranty 7–11 Months",
    "oow_11": "Out of Warranty 11+ Months",
}


def _resolve_bucket(warranty_status, device_age_months) -> str:
    """Map warranty status + age bracket onto a Price Master column suffix."""
    age = _resolve_age_months(device_age_months)
    is_iw = warranty_status == "In Warranty"

    if is_iw and age <= 3:
        return "iw_0_3"
    if is_iw and age <= 6:
        return "iw_0_6"
    if is_iw and age <= 11:
        return "iw_6_11"
    return "oow_11"


def _bucket_grade_letters(bucket: str) -> list[str]:
    """Grades that have a price column in this band.

    The 0–3 month band has no Grade D column on Buyback Price Master, so a
    freshly-bought handset with a cracked screen cannot currently be graded D.
    Adding that column is tracked separately; this helper keeps every reader of
    the grid honest about which columns actually exist today.
    """
    return ["a", "b", "c"] if bucket == "iw_0_3" else ["a", "b", "c", "d"]

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
#
# The POS quick-grading endpoint sends six short keys (screen/body/buttons/
# charging/camera/speaker_mic). These previously pointed at diag-* codes that
# exist nowhere in the question bank, so five of the six resolved to nothing
# and deducted 0% in silence. They now point at the codes actually loaded on
# the site.
#
# "body" has no boolean equivalent: cosmetic condition is captured by the
# multi-option ladders (touch_glass_condition, back_panel_condition,
# center_or_side_panel_condition), which a pass/fail flag cannot select an
# option from. It is deliberately absent so the lookup raises rather than
# quietly scoring a damaged body at zero.
_DIAG_CODE_ALIASES = {
    "screen": "screen",
    "camera": "camera_test",
    "speaker_mic": "speaker",
    "speaker": "speaker",
    "mic": "microphone",
    "microphone": "microphone",
    "charging": "cha",
    "charge": "cha",
    "battery": "battery",
    "buttons": "power_button",
    "power_button": "power_button",
    "volume": "volume_buttons",
    "wifi": "wifi",
    "bluetooth": "bluetooth",
    "gps": "gps",
    "flash": "flash_light",
    "fingerprint": "finger_print",
    "proximity": "proximity_sensor",
}


def _resolve_diag_question(test_code):
    """Return (name, fault_code) for a diagnostic code, or (None, None).

    Tries the code as given, then the POS alias. The old `diag-<key>` guess is
    gone — it never matched anything on this site and only served to make a
    miss look like a deliberate zero.
    """
    key = (test_code or "").strip()
    if not key:
        return None, None

    candidates = [key]
    alias = _DIAG_CODE_ALIASES.get(key.lower())
    if alias and alias != key:
        candidates.append(alias)

    for code in candidates:
        row = frappe.db.get_value(
            "Buyback Question Bank",
            {"question_code": code, "disabled": 0},
            ["name", "fault_code"],
            as_dict=True,
        )
        if row:
            return row.name, row.get("fault_code")

    # A condition check the operator answered that maps to no question is a
    # configuration gap, not a clean bill of health. Make it visible.
    frappe.log_error(
        message=(
            f"Diagnostic code '{key}' resolved to no enabled Buyback Question Bank "
            f"row (tried: {', '.join(candidates)}). Its result was ignored and "
            f"deducted nothing."
        ),
        title="Buyback: unmapped diagnostic code",
    )
    return None, None


def _get_diagnostic_deduction(diagnostic_test, base_price):
    """Calculate deduction from an automated diagnostic test result.    """
    test_code = diagnostic_test.get("test_code")
    result = diagnostic_test.get("result")

    if not test_code or not result:
        return None

    question, fault_code = _resolve_diag_question(test_code)
    if not question:
        return None

    result_str = str(result).strip().casefold()
    all_opts = frappe.get_all(
        "Buyback Question Option",
        filters={"parent": question},
        fields=["option_label", "option_value", "price_impact_percent"],
    )

    # Prefer the option explicitly configured for this question. This matters
    # because Yes can mean either a healthy result ("Camera working?") or a
    # defect ("Water damage visible?"). A global Yes -> Fail conversion loses
    # that question-specific meaning.
    option = next(
        (
            opt
            for opt in all_opts
            if str(opt.option_value or "").strip().casefold() == result_str
        ),
        None,
    )

    # Compatibility for old diagnostic masters which still use
    # Pass/Fail/Partial. Current Yes/No masters always take the exact path.
    if not option:
        legacy_value = {"yes": "Fail", "no": "Pass"}.get(result_str, result)
        legacy_key = str(legacy_value).strip().casefold()
        option = next(
            (
                opt
                for opt in all_opts
                if str(opt.option_value or "").strip().casefold() == legacy_key
            ),
            None,
        )

    if not option or not option.get("price_impact_percent"):
        return None

    deduction_amount = abs(base_price * flt(option.get("price_impact_percent")) / 100)

    return {
        "label": f"{test_code}: {option.get('option_label') or result}",
        "amount": deduction_amount,
        "type": "diagnostic_test",
        "percent": abs(option.get("price_impact_percent")),
        "_key": _deduction_key(question, fault_code),
    }

def _get_question_deduction(response, base_price):
    """Calculate deduction from a single question response."""
    question_code = response.get("question_code")
    answer_value = response.get("answer_value")

    if not question_code or not answer_value:
        return None

    qrow = frappe.db.get_value(
        "Buyback Question Bank",
        {"question_code": question_code, "disabled": 0},
        ["name", "fault_code"],
        as_dict=True,
    )
    if not qrow:
        return None
    question, fault_code = qrow.name, qrow.get("fault_code")

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
        "_key": _deduction_key(question, fault_code),
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

    bucket = _resolve_bucket(warranty_status, device_age_months)
    grade_letters = _bucket_grade_letters(bucket)

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

def _get_phone_dead_price(item_code, warranty_status=None, device_age_months=None):
    """Return the Phone Dead price — one value, whatever the age or warranty.

    A handset that does not power on is worth its salvage value; that does not
    change because it is 2 months old rather than 20, or because warranty has
    not lapsed. The warranty/age arguments are accepted and ignored so callers
    (and the grade-price helpers beside this one) keep a uniform signature.
    """
    return flt(
        frappe.db.get_value(
            "Buyback Price Master", {"item_code": item_code}, "phone_dead_price"
        )
    )


def _get_scrap_price(item_code, warranty_status=None, device_age_months=None):
    """Return the Scrap price — one value, whatever the age or warranty.

    Scrap is the floor applied when deductions drop the quote below the
    bucket's minimum grade price. The THRESHOLD stays band-specific (see
    _get_min_grade_price); only the salvage value itself is flat.
    """
    return flt(
        frappe.db.get_value(
            "Buyback Price Master", {"item_code": item_code}, "scrap_price"
        )
    )


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

    bucket = _resolve_bucket(warranty_status, device_age_months)
    lowest = _bucket_grade_letters(bucket)[-1]
    return flt(bpm.get(f"{lowest}_grade_{bucket}"))
