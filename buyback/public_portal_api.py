import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, flt

from buyback.api import _calculate_estimate
from buyback.utils import (
    get_buyback_setting_value,
    get_int_setting,
    increment_fixed_window,
    parse_public_response_rows,
    validate_bounded_text,
    validate_indian_phone,
)


_PUBLIC_QUOTE_OTP_PURPOSE = "Buyback Customer Approval"


def _public_client_ip() -> str:
    request = getattr(frappe.local, "request", None)
    return str(
        getattr(request, "remote_addr", None)
        or getattr(frappe.local, "request_ip", None)
        or "unknown"
    )


def _enforce_public_quote_rate_limit(kind: str, mobile_no: str) -> None:
    setting = (
        "public_quote_otp_rate_limit"
        if kind == "otp"
        else "public_quote_submit_rate_limit"
    )
    attempt_limit = min(get_int_setting(setting, 10), 1000)
    window_seconds = min(get_int_setting("public_quote_rate_window_seconds", 300), 86400)
    identity = f"{_public_client_ip()}:{mobile_no}:{_PUBLIC_QUOTE_OTP_PURPOSE}"
    attempts = increment_fixed_window(
        f"public-quote-{kind}", identity, window_seconds
    )
    if attempts > attempt_limit:
        frappe.throw(
            _("Too many public quote requests. Please try again later."),
            frappe.RateLimitExceededError,
            title=_("Rate Limit Exceeded"),
        )


def _public_quote_service_user() -> str:
    user = str(get_buyback_setting_value("public_quote_service_user", "") or "").strip()
    row = (
        frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
        if user
        else None
    )
    if (
        not row
        or not row.enabled
        or row.user_type != "System User"
        or not frappe.has_permission("Buyback Assessment", "create", user=user)
    ):
        frappe.throw(
            _("Public quote intake is not configured. Please contact the store."),
            frappe.ValidationError,
            title=_("Configuration Required"),
        )
    return user


def _validate_buyback_eligible_item(item_code: str) -> None:
    """Hard-gate: item must exist, not be disabled, and be flagged eligible.

    Market standard (Cashify / Samsung Exchange / Apple Trade In / Best Buy
    Trade-In): only pre-approved SKUs with an active price policy are surfaced
    to the public trade-in portal. Public callers cannot bypass this.
    """
    if not item_code or not frappe.db.exists("Item", item_code):
        frappe.throw(_("Select a valid device model."), title=_("Missing Device"))

    item_row = frappe.db.get_value(
        "Item",
        item_code,
        ["disabled", "is_sales_item", "ch_is_buyback_eligible"],
        as_dict=True,
    ) or {}
    if item_row.get("disabled") or not item_row.get("is_sales_item"):
        frappe.throw(
            _("This device model is not available."),
            title=_("Not Available"),
        )
    # ch_is_buyback_eligible may be missing on first migrate — treat as ineligible.
    if not item_row.get("ch_is_buyback_eligible"):
        frappe.throw(
            _("This device is not eligible for buyback / trade-in."),
            title=_("Not Eligible"),
        )


def _resolve_grade(grade: str | None) -> tuple[str, str]:
    grade = (grade or "").strip()
    if not grade:
        row = frappe.db.get_value(
            "Grade Master",
            {"grade_name": "A"},
            ["name", "grade_name"],
            as_dict=True,
        )
        return (row.name, row.grade_name) if row else ("", "A")

    if frappe.db.exists("Grade Master", grade):
        return grade, frappe.db.get_value("Grade Master", grade, "grade_name") or grade

    row = frappe.db.get_value(
        "Grade Master",
        {"grade_name": grade},
        ["name", "grade_name"],
        as_dict=True,
    )
    if row:
        return row.name, row.grade_name or row.name

    frappe.throw(_("Grade {0} was not found.").format(grade), title=_("Invalid Grade"))


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=120, seconds=60, ip_based=True)
def search_buyback_items(query: str = "", limit: int = 12) -> list[dict]:
    limit = max(1, min(cint(limit or 12), 20))
    # Market-standard eligibility gate: only expose SKUs explicitly opted-in
    # to buyback via Item.ch_is_buyback_eligible (Cashify/Samsung/Apple pattern).
    filters = {
        "disabled": 0,
        "is_sales_item": 1,
        "ch_is_buyback_eligible": 1,
    }
    or_filters = []
    query = validate_bounded_text(query, _("Search query"), 100)
    if query:
        or_filters = [
            ["item_code", "like", f"%{query}%"],
            ["item_name", "like", f"%{query}%"],
            ["brand", "like", f"%{query}%"],
        ]

    rows = frappe.get_all(
        "Item",
        filters=filters,
        or_filters=or_filters or None,
        fields=["name", "item_code", "item_name", "brand", "item_group", "image"],
        order_by="modified desc",
        limit_page_length=limit,
    )
    for row in rows:
        parts = [part for part in [row.get("brand"), row.get("item_name") or row.get("item_code")] if part]
        row["label"] = " - ".join(parts)
    return rows


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60, ip_based=True)
def get_quote_grades() -> list[dict]:
    return frappe.get_all(
        "Grade Master",
        fields=["name", "grade_name", "description", "display_order"],
        order_by="display_order asc, grade_name asc",
        limit_page_length=min(get_int_setting("public_response_row_limit", 100), 500),
    )


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=120, seconds=60, ip_based=True)
def get_public_quote_estimate(
    item_code: str,
    grade: str | None = None,
    warranty_status: str | None = None,
    device_age_months: str | None = None,
    responses: str | None = None,
) -> dict:
    item_code = validate_bounded_text(item_code, _("Item"), 140, required=True)
    grade = validate_bounded_text(grade, _("Grade"), 140)
    warranty_status = validate_bounded_text(warranty_status, _("Warranty Status"), 50)
    device_age_months = validate_bounded_text(device_age_months, _("Device Age"), 4)
    response_rows = parse_public_response_rows(responses)
    _validate_buyback_eligible_item(item_code)

    grade_link, grade_label = _resolve_grade(grade)
    item_meta = frappe.db.get_value(
        "Item",
        item_code,
        ["brand", "item_group", "item_name"],
        as_dict=True,
    ) or {}

    estimate = _calculate_estimate(
        item_code=item_code,
        grade=grade_link or grade_label,
        warranty_status=(warranty_status or "").strip() or None,
        device_age_months=(device_age_months or "").strip() or None,
        responses=response_rows,
        brand=item_meta.get("brand"),
        item_group=item_meta.get("item_group"),
    )
    estimate.update(
        {
            "item_code": item_code,
            "item_name": item_meta.get("item_name") or item_code,
            "brand": item_meta.get("brand") or "",
            "item_group": item_meta.get("item_group") or "",
            "grade": {"name": grade_link, "label": grade_label},
        }
    )
    return estimate


@frappe.whitelist(allow_guest=True, methods=["POST"])
def request_public_quote_otp(mobile_no: str) -> dict:
    mobile_no = validate_indian_phone(mobile_no, "Mobile No")
    _enforce_public_quote_rate_limit("otp", mobile_no)
    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

    otp_code = CHOTPLog.generate_otp(mobile_no, _PUBLIC_QUOTE_OTP_PURPOSE)
    from buyback.buyback.whatsapp_notifications import send_otp

    delivery = send_otp(mobile_no, otp_code, _PUBLIC_QUOTE_OTP_PURPOSE)
    if not any(delivery.values()):
        frappe.throw(_("OTP delivery is not configured. Please contact the store."))
    return {"status": "sent", "mobile_no_masked": f"******{mobile_no[-4:]}"}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def submit_public_quote_request(
    customer_name: str,
    mobile_no: str,
    item_code: str,
    grade: str | None = None,
    warranty_status: str | None = None,
    device_age_months: str | None = None,
    imei_serial: str | None = None,
    remarks: str | None = None,
    responses: str | None = None,
    otp_code: str | None = None,
) -> dict:
    customer_name = validate_bounded_text(
        customer_name, _("Customer Name"), 140, required=True
    )
    mobile_no = validate_indian_phone(mobile_no, "Mobile No")
    _enforce_public_quote_rate_limit("submit", mobile_no)
    otp_code = validate_bounded_text(otp_code, _("OTP"), 10, required=True)
    item_code = validate_bounded_text(item_code, _("Item"), 140, required=True)
    grade = validate_bounded_text(grade, _("Grade"), 140)
    warranty_status = validate_bounded_text(warranty_status, _("Warranty Status"), 50)
    device_age_months = validate_bounded_text(device_age_months, _("Device Age"), 4)
    imei_serial = validate_bounded_text(imei_serial, _("IMEI / Serial Number"), 64)
    remarks = validate_bounded_text(remarks, _("Remarks"), 500)
    response_rows = parse_public_response_rows(responses)
    service_user = _public_quote_service_user()
    pending_otp = frappe.db.get_value(
        "CH OTP Log",
        {"mobile_no": mobile_no, "purpose": _PUBLIC_QUOTE_OTP_PURPOSE, "status": "Pending"},
        "name",
        order_by="creation desc",
        for_update=True,
    )
    if not pending_otp:
        frappe.throw(_("OTP is invalid or has already been used."), frappe.PermissionError)
    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

    verification = CHOTPLog.verify_otp(mobile_no, _PUBLIC_QUOTE_OTP_PURPOSE, otp_code)
    if not verification.get("valid"):
        frappe.throw(_("OTP is invalid or expired."), frappe.PermissionError)
    frappe.db.set_value("CH OTP Log", pending_otp, "status", "Expired", update_modified=False)
    _validate_buyback_eligible_item(item_code)

    grade_link, grade_label = _resolve_grade(grade)
    pricing = get_public_quote_estimate(
        item_code=item_code,
        grade=grade_link or grade_label,
        warranty_status=warranty_status,
        device_age_months=device_age_months,
        responses=response_rows,
    )
    customer = frappe.db.get_value("Customer", {"mobile_no": mobile_no}, "name")

    doc = frappe.get_doc(
        {
            "doctype": "Buyback Assessment",
            "source": "Web",
            "customer": customer,
            "customer_name": customer_name,
            "mobile_no": mobile_no,
            "item": item_code,
            "imei_serial": imei_serial,
            "device_age_months": device_age_months,
            "warranty_status": warranty_status,
            "estimated_grade": grade_link,
            "estimated_price": flt(pricing.get("estimated_price")),
            "quoted_price": flt(pricing.get("estimated_price")),
            "remarks": remarks,
        }
    )
    if response_rows:
        doc.responses = response_rows

    original_user = frappe.session.user
    try:
        frappe.set_user(service_user)
        doc.insert()
    finally:
        frappe.set_user(original_user)

    return {
        "name": doc.name,
        "assessment_id": doc.assessment_id,
        "status": doc.status,
        "item": doc.item,
        "item_name": frappe.db.get_value("Item", doc.item, "item_name") or doc.item,
        "estimated_grade": grade_label,
        "estimated_price": flt(doc.estimated_price),
        "quoted_price": flt(doc.quoted_price),
        "expires_on": str(doc.expires_on) if doc.expires_on else None,
    }
