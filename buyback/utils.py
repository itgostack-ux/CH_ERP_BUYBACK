"""
Shared utilities for the Buyback app.
Pattern: India Compliance keeps utils.py at app root with reusable helpers.
"""

import hashlib
import json
import re

import frappe
from frappe import _
from frappe.model.naming import getseries
from frappe.utils import cint, now_datetime


_PRIVILEGED_ROLE = "System Manager"

# Runtime code refers only to setting field names.  The role values below are
# bootstrap defaults for sites that have not migrated the corresponding
# Buyback Settings fields yet; after migration every set is editable in the
# singleton and takes precedence.
ROLE_SETTING_DEFAULTS: dict[str, frozenset[str]] = {
    "app_access_roles": frozenset(
        {"Buyback Agent", "Buyback Store Manager", "Buyback Manager", "Buyback Auditor", "Buyback Admin"}
    ),
    "order_operation_roles": frozenset(
        {"Buyback Agent", "Buyback Store Manager", "Buyback Manager", "Buyback Admin"}
    ),
    "payment_operation_roles": frozenset(
        {"Buyback Store Manager", "Buyback Manager", "Buyback Admin"}
    ),
    "manager_approval_roles": frozenset({"Buyback Manager", "Buyback Admin"}),
    "exchange_creation_roles": frozenset(
        {"Buyback Agent", "Buyback Store Manager", "Buyback Manager", "Buyback Admin"}
    ),
    "otp_bypass_roles": frozenset(
        {"Buyback Store Manager", "Buyback Manager", "Buyback Admin"}
    ),
    "imei_validation_roles": frozenset(
        {"Buyback Agent", "Buyback Store Manager", "Buyback Manager", "Buyback Admin"}
    ),
    "pickup_request_roles": frozenset(
        {"Buyback Store Manager", "Buyback Manager", "Buyback Admin"}
    ),
    "assessment_operation_roles": frozenset(
        {"Buyback Agent", "Buyback Store Manager", "Buyback Manager", "Buyback Admin"}
    ),
    "inspection_operation_roles": frozenset(
        {"Buyback Agent", "Buyback Store Manager", "Buyback Manager", "Buyback Admin"}
    ),
    "dashboard_roles": frozenset({"Buyback Manager", "Buyback Auditor", "Buyback Admin"}),
    "scorecard_roles": frozenset({"Buyback Manager", "Buyback Auditor", "Buyback Admin"}),
    "imei_history_roles": frozenset(
        {"Buyback Agent", "Buyback Store Manager", "Buyback Manager", "Buyback Auditor", "Buyback Admin"}
    ),
    "customer_lookup_roles": frozenset(
        {"Buyback Agent", "Buyback Store Manager", "Buyback Manager", "Buyback Auditor", "Buyback Admin"}
    ),
    "refurbishment_creation_roles": frozenset(
        {"Buyback Store Manager", "Buyback Manager", "Buyback Admin"}
    ),
    "refurbishment_operation_roles": frozenset(
        {"Buyback Store Manager", "Buyback Manager", "Buyback Admin"}
    ),
    "refurbishment_restock_roles": frozenset(
        {"Buyback Manager", "Buyback Admin"}
    ),
    "sla_alert_roles": frozenset(
        {"Buyback Store Manager", "Buyback Manager", "Buyback Admin"}
    ),
    "approval_alert_roles": frozenset({"Buyback Manager", "Buyback Admin"}),
    "fraud_alert_roles": frozenset({"Buyback Auditor", "Buyback Admin"}),
    "cash_alert_roles": frozenset({"Buyback Manager", "Buyback Admin"}),
    "performance_alert_roles": frozenset(
        {"Buyback Store Manager", "Buyback Manager"}
    ),
}


_NUMERIC_EXTERNAL_ID_SERIES = {
    ("Buyback Question Bank", "question_id"): "BUYBACK-QUESTION-ID",
    ("Buyback Pricing Rule", "pricing_rule_id"): "BUYBACK-PRICING-RULE-ID",
    ("Buyback Price Master", "buyback_price_id"): "BUYBACK-PRICE-ID",
    ("Buyback Audit Log", "audit_id"): "BUYBACK-AUDIT-ID",
    ("Buyback Assessment", "assessment_id"): "BUYBACK-ASSESSMENT-ID",
    ("Buyback Inspection", "inspection_id"): "BUYBACK-INSPECTION-ID",
    ("Grade Master", "grade_id"): "BUYBACK-GRADE-ID",
    ("Buyback Checklist Template", "checklist_id"): "BUYBACK-CHECKLIST-ID",
    ("Buyback Exchange Order", "exchange_id"): "BUYBACK-EXCHANGE-ID",
    ("Buyback Order", "order_id"): "BUYBACK-ORDER-ID",
}


def _rate_limit_redis_key(namespace: str, identity: object) -> bytes:
    identity_digest = hashlib.sha256(str(identity).encode()).hexdigest()
    return frappe.cache().make_key(f"buyback-limit:v2:{namespace}:{identity_digest}")


def increment_fixed_window(namespace: str, identity: object, window_seconds: int) -> int:
    """Atomically reserve an attempt in an expiring Redis counter."""
    window_seconds = max(1, int(window_seconds))
    cache = frappe.cache()
    key = _rate_limit_redis_key(namespace, identity)
    cache.set(key, 0, nx=True, ex=window_seconds)
    count = int(cache.incrby(key, 1))
    if count == 1:
        cache.expire(key, window_seconds)
    return count


def clear_fixed_window(namespace: str, identity: object) -> None:
    frappe.cache().delete(_rate_limit_redis_key(namespace, identity))


def next_numeric_external_id(doctype: str, fieldname: str) -> int:
    series_key = _NUMERIC_EXTERNAL_ID_SERIES.get((doctype, fieldname))
    if not series_key:
        frappe.throw(_("Unsupported numeric identifier series."))

    existing_max = cint(
        frappe.db.get_value(doctype, {}, f"MAX({fieldname})", order_by=None) or 0
    )
    frappe.db.sql(
        """
        INSERT INTO `tabSeries` (`name`, `current`)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE
            `current` = GREATEST(`current`, VALUES(`current`))
        """,
        (series_key, existing_max),
    )
    return cint(getseries(series_key, 10))


# ---------------------------------------------------------------------------
# Indian phone number validation — canonical home is ch_item_master.utils
# Re-exported here for backward compatibility.
# ---------------------------------------------------------------------------
from ch_item_master.ch_item_master.utils import (  # noqa: F401
    normalize_indian_phone,
    validate_indian_phone,
)


def log_audit(
    action: str,
    reference_doctype: str,
    reference_name: str,
    old_value: dict | None = None,
    new_value: dict | None = None,
    reason: str | None = None,
):
    """
    Create a Buyback Audit Log entry.

    Centralised helper — replaces the duplicated ``_log_audit()`` that was
    copy-pasted into every controller module.
    """
    meta = frappe.get_meta("Buyback Audit Log")

    # Keep action compatible with strict Select options in customized sites.
    action_value = action
    action_df = meta.get_field("action")
    if action_df and action_df.fieldtype == "Select":
        allowed_actions = {row.strip() for row in (action_df.options or "").split("\n") if row.strip()}
        if allowed_actions and action_value not in allowed_actions:
            fallback = "Settlement Done" if "Settlement Done" in allowed_actions else next(iter(allowed_actions))
            action_value = fallback
            reason = f"{reason + ' | ' if reason else ''}Original Action: {action}"

    payload = {
        "doctype": "Buyback Audit Log",
        "action": action_value,
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "user": frappe.session.user,
        "timestamp": now_datetime(),
        "ip_address": getattr(frappe.local, "request_ip", None),
        "old_value": json.dumps(old_value) if old_value else None,
        "new_value": json.dumps(new_value) if new_value else None,
        "reason": reason,
    }

    # Some sites keep a custom Select field `condition_grade` on Buyback Audit Log.
    # Normalize Grade Master link (e.g. GRD-00003) to label (e.g. A - Like New).
    grade_df = meta.get_field("condition_grade")
    if grade_df and reference_doctype == "Buyback Order" and frappe.db.exists("Buyback Order", reference_name):
        raw_grade = frappe.db.get_value("Buyback Order", reference_name, "condition_grade")
        if raw_grade:
            grade_name = frappe.db.get_value("Grade Master", raw_grade, "grade_name") or raw_grade
            payload["condition_grade"] = grade_name

    frappe.get_doc(payload).insert(ignore_permissions=True)


def get_buyback_settings() -> "frappe.Document":
    """Return the cached Buyback Settings singleton."""
    return frappe.get_cached_doc("Buyback Settings")


def get_buyback_setting_value(fieldname: str, default=None):
    try:
        meta = frappe.get_meta("Buyback Settings")
        if not meta.has_field(fieldname):
            return default
        value = frappe.get_cached_value("Buyback Settings", None, fieldname)
        if value is None:
            field = meta.get_field(fieldname)
            value = field.default if field else None
    except Exception:
        return default
    return default if value is None else value


def get_role_setting(fieldname: str, defaults=()) -> frozenset[str]:
    value = get_buyback_setting_value(fieldname)
    if value is None:
        return frozenset(defaults)
    return frozenset(role.strip() for role in re.split(r"[,\n]", value) if role.strip())


def is_privileged_user(user: str | None = None) -> bool:
    """Return whether ``user`` has the immutable platform-wide bypass.

    ``Administrator`` is a principal rather than a role in Frappe.  System
    Manager remains an explicit role.  Neither can be configured out of a
    Buyback action gate.
    """
    user = user or frappe.session.user
    if user == "Administrator":
        return True
    if not user or user == "Guest":
        return False
    return _PRIVILEGED_ROLE in set(frappe.get_roles(user))


def filter_enabled_system_users(users, *, limit: int | None = None) -> list[str]:
    """Return enabled desk users from an arbitrary recipient iterable."""
    candidates = list(dict.fromkeys(user for user in (users or []) if user))
    if not candidates:
        return []
    valid = set(
        frappe.get_all(
            "User",
            filters={
                "name": ("in", candidates),
                "enabled": 1,
                "user_type": "System User",
            },
            pluck="name",
        )
    )
    result = [user for user in candidates if user in valid]
    return result[:limit] if limit else result


def has_configured_role(fieldname: str, *, user: str | None = None) -> bool:
    """Return a server-authoritative capability for a Buyback action."""
    user = user or frappe.session.user
    if not user or user == "Guest":
        return False
    if is_privileged_user(user):
        return True
    defaults = ROLE_SETTING_DEFAULTS.get(fieldname)
    if defaults is None:
        return False
    return bool(set(frappe.get_roles(user)).intersection(get_role_setting(fieldname, defaults)))


def has_app_permission(user: str | None = None) -> bool:
    return has_configured_role("app_access_roles", user=user)


def require_configured_role(
    fieldname: str,
    *,
    user: str | None = None,
    action: str | None = None,
) -> None:
    """Require a role configured for a Buyback action.

    Unknown action fields fail closed.  Administrator and System Manager are
    immutable bypass identities; all other users need a configured role.
    """
    user = user or frappe.session.user
    defaults = ROLE_SETTING_DEFAULTS.get(fieldname)
    if defaults is None:
        frappe.throw(_("Unknown Buyback permission action."), frappe.PermissionError)
    if has_configured_role(fieldname, user=user):
        return
    frappe.throw(
        _("You do not have a configured role to {0}.").format(action or _("perform this action")),
        frappe.PermissionError,
    )


def get_buyback_data_scope(user: str | None = None) -> dict:
    """Resolve the caller's location scope for Buyback data APIs.

    ``bypass`` is true for the immutable privileged identities and for any
    site-configured global scope role.  Missing CH User Scope rows resolve to
    empty sets and therefore fail closed in the helpers below.
    """
    user = user or frappe.session.user
    if is_privileged_user(user):
        return {"bypass": True, "stores": set(), "warehouses": set(), "companies": set()}
    if not user or user == "Guest":
        return {"bypass": False, "stores": set(), "warehouses": set(), "companies": set()}
    try:
        from ch_erp15.ch_erp15.scope import get_user_scope

        resolved = get_user_scope(user)
    except (ImportError, ModuleNotFoundError):
        resolved = {}
    return {
        "bypass": bool(resolved.get("bypass")),
        "stores": set(resolved.get("stores") or set()),
        "warehouses": set(resolved.get("warehouses") or set()),
        "companies": set(resolved.get("companies") or set()),
    }


def build_buyback_scope_sql(
    *,
    store_field: str | None = None,
    company_field: str | None = None,
    prefix: str = "bb_scope",
    user: str | None = None,
) -> tuple[str, dict]:
    """Return a parameterized SQL scope clause and its values.

    Store/warehouse is the most-specific axis.  A company that was derived
    from one scoped store must not accidentally expose sibling stores, so the
    company axis is used only when no store/warehouse scope exists.
    """
    scope = get_buyback_data_scope(user)
    if scope["bypass"]:
        return "1=1", {}

    locations = sorted(scope["stores"] | scope["warehouses"])
    if locations and store_field:
        params = {f"{prefix}_location_{idx}": value for idx, value in enumerate(locations)}
        placeholders = ", ".join(f"%({key})s" for key in params)
        return f"{store_field} IN ({placeholders})", params

    companies = sorted(scope["companies"])
    if companies and company_field:
        params = {f"{prefix}_company_{idx}": value for idx, value in enumerate(companies)}
        placeholders = ", ".join(f"%({key})s" for key in params)
        return f"{company_field} IN ({placeholders})", params

    return "1=0", {}


def assert_buyback_scope(
    *,
    store: str | None = None,
    warehouse: str | None = None,
    company: str | None = None,
    user: str | None = None,
) -> None:
    """Reject a named-record action outside the caller's location scope."""
    scope = get_buyback_data_scope(user)
    if scope["bypass"]:
        return

    locations = scope["stores"] | scope["warehouses"]
    supplied_location = store or warehouse
    if supplied_location and locations:
        if (store and store in locations) or (warehouse and warehouse in locations):
            return
        frappe.throw(_("This record is outside your assigned store scope."), frappe.PermissionError)

    if not supplied_location and company and company in scope["companies"]:
        return

    frappe.throw(_("This record is outside your assigned location scope."), frappe.PermissionError)


def require_scoped_document_action(doc, fieldname: str, action: str) -> None:
    """Authorize and lock a named Buyback document before a bound mutation."""
    require_configured_role(fieldname, action=action)
    doc.check_permission("write")
    assert_buyback_scope(
        store=doc.get("store"),
        warehouse=doc.get("warehouse"),
        company=doc.get("company"),
    )
    frappe.db.get_value(doc.doctype, doc.name, "name", for_update=True)
    doc.reload()


def get_int_setting(fieldname: str, default: int, minimum: int = 1) -> int:
    value = cint(get_buyback_setting_value(fieldname))
    return value if value >= minimum else default


def new_scheduler_alert_budget() -> dict[str, int]:
    return {"remaining": min(get_int_setting("scheduler_alert_limit", 50), 500)}


def claim_scheduler_alert(budget: dict[str, int] | None) -> bool:
    if budget is None:
        return True
    if budget.get("remaining", 0) <= 0:
        return False
    budget["remaining"] -= 1
    return True


def validate_bounded_text(
    value,
    field_label: str,
    max_length: int,
    *,
    required: bool = False,
) -> str:
    """Normalize text and reject oversized public input without truncation."""
    text = str(value or "").strip()
    if required and not text:
        frappe.throw(_("{0} is required.").format(field_label), frappe.ValidationError)
    if len(text) > max_length:
        frappe.throw(
            _("{0} cannot exceed {1} characters.").format(field_label, max_length),
            frappe.ValidationError,
        )
    return text


def parse_public_response_rows(responses) -> list[dict]:
    """Parse a bounded public assessment response list."""
    if responses in (None, "", []):
        return []

    payload_limit = min(get_int_setting("public_payload_max_chars", 20000), 100000)
    row_limit = min(get_int_setting("public_response_row_limit", 100), 500)
    if isinstance(responses, str):
        if len(responses) > payload_limit:
            frappe.throw(
                _("Assessment responses exceed the configured payload limit."),
                frappe.ValidationError,
            )
        try:
            rows = json.loads(responses)
        except (TypeError, ValueError):
            frappe.throw(_("Assessment responses must be valid JSON."), frappe.ValidationError)
    else:
        rows = responses
        try:
            encoded = json.dumps(rows, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            frappe.throw(_("Assessment responses are invalid."), frappe.ValidationError)
        if len(encoded) > payload_limit:
            frappe.throw(
                _("Assessment responses exceed the configured payload limit."),
                frappe.ValidationError,
            )

    if not isinstance(rows, list) or len(rows) > row_limit:
        frappe.throw(
            _("Assessment responses must be a list of at most {0} rows.").format(row_limit),
            frappe.ValidationError,
        )
    if any(not isinstance(row, dict) for row in rows):
        frappe.throw(_("Each assessment response must be an object."), frappe.ValidationError)
    return rows


def sync_customer_identity(doc) -> None:
    if not doc.customer or (doc.get("ch_customer_id") and doc.get("ch_membership_id")):
        return
    customer = frappe.db.get_value(
        "Customer", doc.customer, ["ch_customer_id", "ch_membership_id"], as_dict=True
    )
    if not customer:
        return
    if not doc.get("ch_customer_id"):
        doc.ch_customer_id = customer.ch_customer_id
    if not doc.get("ch_membership_id"):
        doc.ch_membership_id = customer.ch_membership_id


def update_customer_mobile_if_missing(customer: str | None, mobile_no: str | None) -> None:
    """Populate a linked Customer's primary mobile without overwriting one."""
    if not customer or not mobile_no:
        return
    if not frappe.db.get_value("Customer", customer, "mobile_no"):
        frappe.db.set_value("Customer", customer, "mobile_no", mobile_no)


def resolve_store_bin_warehouse(store, company=None, bin_type="Sellable"):
    """Resolve a store's child Warehouse for a given ``ch_bin_type``.

    A store is a group Warehouse (``self.store`` on Buyback / Exchange orders)
    whose children are the operational bins — ``Sellable`` (the store's own
    selling stock), ``Buyback`` (device quarantine / refurb intake), ``Damaged``,
    ``Demo``, etc. Returns the matching child, or ``store`` itself as a fallback
    when the bin isn't provisioned yet.
    """
    if not store:
        return None
    filters = {"parent_warehouse": store, "ch_bin_type": bin_type}
    if company:
        filters["company"] = company
    return frappe.db.get_value("Warehouse", filters, "name") or store
