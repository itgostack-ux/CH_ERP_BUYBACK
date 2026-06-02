import json

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, flt

from buyback.api import get_estimate
from buyback.utils import validate_indian_phone


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
    filters = {"disabled": 0, "is_sales_item": 1}
    or_filters = []
    query = (query or "").strip()
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
    item_code = (item_code or "").strip()
    if not item_code or not frappe.db.exists("Item", item_code):
        frappe.throw(_("Select a valid device model."), title=_("Missing Device"))

    grade_link, grade_label = _resolve_grade(grade)
    item_meta = frappe.db.get_value(
        "Item",
        item_code,
        ["brand", "item_group", "item_name"],
        as_dict=True,
    ) or {}

    estimate = get_estimate(
        item_code=item_code,
        grade=grade_link or grade_label,
        warranty_status=(warranty_status or "").strip() or None,
        device_age_months=(device_age_months or "").strip() or None,
        responses=responses,
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


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=10, seconds=300, methods=["POST"], ip_based=True)
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
) -> dict:
    mobile_no = validate_indian_phone(mobile_no, "Mobile No")
    item_code = (item_code or "").strip()
    if not frappe.db.exists("Item", item_code):
        frappe.throw(_("Select a valid device model."), title=_("Missing Device"))

    grade_link, grade_label = _resolve_grade(grade)
    pricing = get_public_quote_estimate(
        item_code=item_code,
        grade=grade_link or grade_label,
        warranty_status=warranty_status,
        device_age_months=device_age_months,
        responses=responses,
    )
    customer = frappe.db.get_value("Customer", {"mobile_no": mobile_no}, "name")

    doc = frappe.get_doc(
        {
            "doctype": "Buyback Assessment",
            "source": "Web",
            "customer": customer,
            "customer_name": (customer_name or "Customer").strip(),
            "mobile_no": mobile_no,
            "item": item_code,
            "imei_serial": (imei_serial or "").strip(),
            "device_age_months": (device_age_months or "").strip(),
            "warranty_status": (warranty_status or "").strip(),
            "estimated_grade": grade_link,
            "estimated_price": flt(pricing.get("estimated_price")),
            "quoted_price": flt(pricing.get("estimated_price")),
            "remarks": (remarks or "").strip()[:500],
        }
    )
    if responses:
        doc.responses = json.loads(responses) if isinstance(responses, str) else responses

    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    return {
        "name": doc.name,
        "assessment_id": doc.assessment_id,
        "status": doc.status,
        "customer": doc.customer,
        "mobile_no": doc.mobile_no,
        "item": doc.item,
        "item_name": frappe.db.get_value("Item", doc.item, "item_name") or doc.item,
        "estimated_grade": grade_label,
        "estimated_price": flt(doc.estimated_price),
        "quoted_price": flt(doc.quoted_price),
        "expires_on": str(doc.expires_on) if doc.expires_on else None,
    }
