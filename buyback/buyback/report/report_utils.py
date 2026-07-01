# Copyright (c) 2026, GoStack and contributors
# Shared SQL helpers for all Buyback reports.
# Eliminates duplicated condition-building and date-filter logic.

import frappe
from frappe.utils import getdate

from ch_erp15.ch_erp15.report_scope import scope_where_clause


def date_condition(field="creation", filters=None, alias=""):
    """Return a SQL date-range clause.

    Args:
        field: column name (e.g. "creation", "p.payment_date")
        filters: dict with from_date / to_date
        alias: optional table alias prefix (e.g. "o.")

    Returns:
        str like "o.creation BETWEEN '2026-01-01' AND '2026-03-06 23:59:59'"
    """
    if not filters:
        return "1=1"
    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    if not from_date or not to_date:
        return "1=1"
    col = f"{alias}{field}" if alias and not field.startswith(alias) else field
    # getdate() validates and normalises to datetime.date; str() always yields YYYY-MM-DD
    from_str = str(getdate(from_date))
    to_str = str(getdate(to_date))
    return f"{col} BETWEEN '{from_str}' AND '{to_str} 23:59:59'"


def standard_conditions(filters=None, alias="", field_map=None):
    """Build WHERE clauses from standard report filters.

    Supports: company, store, brand, item_group, source, settlement_type, inspector, status.
    Uses parameterised-safe frappe.db.escape().

    Args:
        filters: dict of filter values
        alias: table alias prefix (e.g. "o." or "q.")
        field_map: override column names, e.g. {"source": "assessment_source"}.
            Pass ``{"store": None}`` to opt out of scope narrowing when the
            underlying query genuinely has no store column.

    Returns:
        str of AND-prefixed conditions. Always includes CH User Scope
        narrowing (fail-closed) unless field_map explicitly maps ``store``
        to ``None``.
    """
    fm = field_map or {}
    conds = []

    if filters:
        simple_fields = ["company", "store", "brand", "item_group", "source",
                         "settlement_type", "inspector", "status"]

        for key in simple_fields:
            val = filters.get(key)
            if val:
                col = fm.get(key, key)
                if not col:
                    continue
                prefix = f"{alias}" if alias and "." not in col else ""
                conds.append(f"{prefix}{col} = {frappe.db.escape(val)}")

    result = (" AND " + " AND ".join(conds)) if conds else ""

    # Tier 4 — CH User Scope narrowing (fail-closed for scoped users).
    # Applied unconditionally so a caller passing an empty / None filters
    # dict cannot bypass scope. field_map={"store": None} opts out for
    # queries whose underlying table genuinely has no store column.
    store_col = fm.get("store", "store") if "store" in fm else "store"
    if store_col:
        field = f"{alias}{store_col}" if alias and "." not in store_col else store_col
        scope_clause = scope_where_clause(store_field=field)
        if scope_clause is not None:
            result += f" AND {scope_clause}"

    return result


def scope_condition(alias="", store_field="store", pos_profile_field=None,
                    warehouse_field=None):
    """Return an AND-prefixed CH User Scope narrowing for reports that
    don't route through ``standard_conditions``.

    Fail-closed contract:
      * Bypass caller (System Manager / Administrator) → ``""``.
      * Scoped caller with a populated set → ``" AND (<field> IN (...))"``.
      * Scoped caller with an empty set / no matching field → ``" AND 1=0"``.
    """
    kwargs = {}
    if store_field:
        kwargs["store_field"] = f"{alias}{store_field}" if alias and "." not in store_field else store_field
    if pos_profile_field:
        kwargs["pos_profile_field"] = f"{alias}{pos_profile_field}" if alias and "." not in pos_profile_field else pos_profile_field
    if warehouse_field:
        kwargs["warehouse_field"] = f"{alias}{warehouse_field}" if alias and "." not in warehouse_field else warehouse_field
    clause = scope_where_clause(**kwargs)
    return f" AND {clause}" if clause is not None else ""


def in_condition(field, values, alias=""):
    """Build a safe IN (...) clause."""
    if not values:
        return "1=0"
    col = f"{alias}{field}" if alias else field
    escaped = ", ".join(frappe.db.escape(v) for v in values)
    return f"{col} IN ({escaped})"


def sla_minutes(start_col, end_col):
    """SQL expression for minutes between two datetime columns.

    Returns NULL-safe expression that yields minutes as float.
    """
    return (
        f"ROUND(TIMESTAMPDIFF(SECOND, {start_col}, "
        f"COALESCE({end_col}, NOW())) / 60, 1)"
    )


def aging_bucket_case(minutes_expr, buckets):
    """Build a CASE expression that assigns an aging bucket label.

    Args:
        minutes_expr: SQL expression returning minutes
        buckets: list of (min_val, max_val, label) tuples

    Returns:
        SQL CASE expression string
    """
    parts = []
    for lo, hi, label in buckets:
        if hi is None:
            parts.append(f"WHEN {minutes_expr} >= {lo} THEN '{label}'")
        else:
            parts.append(f"WHEN {minutes_expr} >= {lo} AND {minutes_expr} < {hi} THEN '{label}'")
    return f"CASE {' '.join(parts)} ELSE 'Unknown' END"
