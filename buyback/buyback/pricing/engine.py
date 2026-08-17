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

    # Condition decides the grade, the grade decides the cell. When the answers
    # carry grading questions they are authoritative and the caller's `grade`
    # is only a starting point; a caller that passes no grading answers (an
    # API quote, a re-price from a stored grade) keeps the grade it supplied.
    graded = resolve_grade_from_answers(responses)
    if _has_grading_answers(responses):
        grade_letter = graded
    else:
        grade_letter = _grade_letter(grade)
    result["grade_letter"] = grade_letter

    resolved_age = _resolve_age_months(device_age_months)
    base_price = _get_base_price(item_code, grade_letter, warranty_status, device_age_months)
    result["base_price"] = base_price

    if not base_price:
        return result

    # The depreciation sheet prices many faults differently on Apple — a broken
    # charging port is 7% on Android and 8% on an iPhone, Face ID is 25% and has
    # no Android equivalent at all. Resolve the family once per quote.
    brand_family = _resolve_brand_family(item_code)
    result["brand_family"] = brand_family

    # Collect deductions, keyed so the same physical fault cannot be charged
    # twice when it is covered by both an automated test and a customer
    # question (see _deduction_key).
    collected: dict = {}

    for dt in (diagnostic_tests or []):
        _collect_deduction(collected, _get_diagnostic_deduction(dt, base_price, brand_family))

    for resp in (responses or []):
        _collect_deduction(collected, _get_question_deduction(resp, base_price, brand_family))

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

    # Floor: a device is never worth less than what scrapping it returns.
    #
    # This used to compare against the band's LOWEST GRADE price, which made
    # sense only while every quote started from the Grade A cell. Now that the
    # grade selects the cell, a Grade D device starts at the D price — so any
    # deduction at all would have dropped it under that threshold and turned
    # every damaged D handset into scrap. The salvage value is the real floor.
    scrap_price = _get_scrap_price(item_code, warranty_status, device_age_months)
    if scrap_price and estimated < scrap_price:
        estimated = scrap_price
        result["is_scrap"] = True

    estimated = _round_price(estimated)
    result["estimated_price"] = estimated

    # Scrap is the one grade the price decides rather than the condition: the
    # device graded normally, then deductions took it under the band's floor.
    # Every other grade was settled before pricing started.
    if result.get("is_scrap"):
        result["grade_letter"] = "E"

    return result


def _has_grading_answers(responses: list | None) -> bool:
    """Whether any answer belongs to a Grading question."""
    codes = [
        (r.get("question_code") or "").strip()
        for r in (responses or [])
        if (r.get("question_code") or "").strip() and r.get("answer_value")
    ]
    if not codes:
        return False
    return bool(frappe.db.exists(
        "Buyback Question Bank",
        {"question_code": ["in", codes], "question_purpose": "Grading", "disabled": 0},
    ))


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


# Best to worst. A device takes the worst grade any single answer forces.
GRADE_ORDER = ["A", "B", "C", "D"]


def resolve_grade_from_answers(responses: list | None) -> str:
    """Derive the condition grade from the grading questions' answers.

    Condition decides grade; grade decides which price cell is used. This is
    the direction the grading sheet describes, and the reverse of what the
    engine used to do — it priced everything off the Grade A cell, subtracted
    whatever it could find, then read a grade back off the resulting number, so
    the grade an inspector saw was an artefact of arithmetic rather than a
    judgement about the device.

    Each option on a grading question declares the worst grade it still allows
    (`forces_grade`). The device lands on the worst grade any answer forces —
    one cracked screen is enough for D no matter how clean the body is.

    Answers to Deduction and Eligibility questions are ignored here; they never
    move the grade.
    """
    worst = 0  # index into GRADE_ORDER

    for resp in (responses or []):
        code = (resp.get("question_code") or "").strip()
        answer = resp.get("answer_value")
        if not code or not answer:
            continue

        question = frappe.db.get_value(
            "Buyback Question Bank",
            {"question_code": code, "disabled": 0},
            ["name", "question_purpose"],
            as_dict=True,
        )
        if not question or question.question_purpose != "Grading":
            continue

        forced = frappe.db.get_value(
            "Buyback Question Option",
            {"parent": question.name, "option_value": answer},
            "forces_grade",
        )
        if forced and forced in GRADE_ORDER:
            worst = max(worst, GRADE_ORDER.index(forced))

    return GRADE_ORDER[worst]


def _grade_letter(grade) -> str:
    """Accept a Grade Master docname, a bare letter, or nothing.

    Callers pass whichever they have — the assessment holds a Link to Grade
    Master, the POS passes a letter, older code passes None.
    """
    value = (grade or "").strip()
    if not value:
        return "A"
    if value.upper() in GRADE_ORDER:
        return value.upper()
    letter = frappe.db.get_value("Grade Master", value, "grade_name")
    return (letter or "A").upper()


def _resolve_brand_family(item_code: str) -> str:
    """"Apple" or "Android" for an item.

    Deliberately reuses the same resolver the question sets route on, rather
    than adding a second brand-family field. One signal cannot drift out of
    step with itself; two can.
    """
    from buyback.buyback.doctype.buyback_question_set.buyback_question_set import (
        get_device_profile,
    )
    try:
        return get_device_profile(item_code)[0]
    except Exception:
        return "Android"


def _rate_for_family(option: dict, brand_family: str) -> float:
    """Pick the Apple or the standard rate for this option.

    An unset Apple rate means "same as the standard rate", never "free on
    Apple". That distinction has to be made on the value rather than on NULL:
    Frappe's Percent field coerces None to 0.0 on save, so an untouched Apple
    column and a deliberate 0% are the same stored value.

    The cost is that a fault cannot be priced at 0% on Apple while charging on
    Android. Nothing on the depreciation sheet does that — every fault it
    prices per-platform charges on both — and reading a blank column as free
    would silently waive real deductions on every iPhone.
    """
    if brand_family == "Apple":
        apple = flt(option.get("price_impact_percent_apple"))
        if apple:
            return apple
    return flt(option.get("price_impact_percent"))


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
    letter = _grade_letter(grade)

    # The grade selects the cell. This argument used to be accepted and
    # ignored, so every device was priced off the Grade A column and the
    # inspector's grade could not move the payout at all.
    available = _bucket_grade_letters(bucket)
    if letter.lower() not in available:
        # The 0–3 month band has no Grade D column, so a nearly-new handset
        # with a cracked screen has nowhere to land. Fall to the worst grade
        # the band does price, and say so.
        frappe.log_error(
            f"{item_code}: grade {letter} has no column in the {bucket} band; "
            f"priced at grade {available[-1].upper()} instead.",
            "Buyback: grade not priced in band",
        )
        letter = available[-1].upper()

    price = flt(bpm.get(f"{letter.lower()}_grade_{bucket}"))

    # No silent grade fallback. Substituting a different grade's cell when this
    # one is blank quoted the wrong grade's price under the right grade's name,
    # and nothing surfaced the substitution. An unpriced cell is a configuration
    # gap and must be fixed in the master, not papered over here.
    if not price:
        frappe.throw(
            _("No Grade {0} price is configured for {1} in the {2} band. "
              "Set it on the Buyback Price Master before quoting this device.").format(
                letter, item_code, _BUCKET_LABELS.get(bucket, bucket)
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


def _get_diagnostic_deduction(diagnostic_test, base_price, brand_family="Android"):
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
        fields=["option_label", "option_value", "price_impact_percent",
                "price_impact_percent_apple"],
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

    if not option:
        return None

    percent = _rate_for_family(option, brand_family)
    if not percent:
        return None

    return {
        "label": f"{test_code}: {option.get('option_label') or result}",
        "amount": abs(base_price * percent / 100),
        "type": "diagnostic_test",
        "percent": abs(percent),
        "_key": _deduction_key(question, fault_code),
    }

def _get_question_deduction(response, base_price, brand_family="Android"):
    """Calculate deduction from a single question response."""
    question_code = response.get("question_code")
    answer_value = response.get("answer_value")

    if not question_code or not answer_value:
        return None

    qrow = frappe.db.get_value(
        "Buyback Question Bank",
        {"question_code": question_code, "disabled": 0},
        ["name", "fault_code", "applies_to_brand_family"],
        as_dict=True,
    )
    if not qrow:
        return None
    question, fault_code = qrow.name, qrow.get("fault_code")

    # Platform confinement is enforced here as well as by set membership.
    # Sets control what an inspector is ASKED; this controls what can be
    # CHARGED. An answer can still arrive from an API caller, a stale payload
    # or a hand-built request, and Face ID at 25% must never land on an
    # Android quote just because someone posted the code.
    only = (qrow.get("applies_to_brand_family") or "Any").strip()
    if only not in ("Any", "", brand_family):
        return None

    fields = ["option_label", "option_value", "price_impact_percent",
              "price_impact_percent_apple"]

    option = frappe.db.get_value(
        "Buyback Question Option",
        {"parent": question, "option_value": answer_value},
        fields, as_dict=True,
    )
    if not option:
        # Case-insensitive retry: answers arriving from older payloads and the
        # POS do not always match the stored casing.
        answer_key = str(answer_value).strip().lower()
        option = next(
            (
                opt for opt in frappe.get_all(
                    "Buyback Question Option", filters={"parent": question}, fields=fields)
                if (opt.option_value or "").strip().lower() == answer_key
            ),
            None,
        )

    if not option:
        return None

    percent = _rate_for_family(option, brand_family)
    if not percent:
        return None

    return {
        "label": f"{question_code}: {option.get('option_label')}",
        "amount": abs(base_price * percent / 100),
        "type": "question",
        "percent": abs(percent),
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

    Scrap is the hard floor on a quote: a handset is never worth less than
    what breaking it for parts returns. The warranty/age arguments are
    accepted and ignored so this keeps a uniform signature with the grade
    price helpers beside it.
    """
    return flt(
        frappe.db.get_value(
            "Buyback Price Master", {"item_code": item_code}, "scrap_price"
        )
    )
