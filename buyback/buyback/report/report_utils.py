# Copyright (c) 2026, GoStack and contributors
# Shared SQL helpers for all Buyback reports.
# Eliminates duplicated condition-building and date-filter logic.

import frappe
from frappe.utils import getdate


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
    return f"{col} BETWEEN '{getdate(from_date)}' AND '{getdate(to_date)} 23:59:59'"


def standard_conditions(filters=None, alias="", field_map=None):
    """Build WHERE clauses from standard report filters.

    Supports: company, store, brand, item_group, source, settlement_type, inspector, status.
    Uses parameterised-safe frappe.db.escape().

    Args:
        filters: dict of filter values
        alias: table alias prefix (e.g. "o." or "q.")
        field_map: override column names, e.g. {"source": "assessment_source"}

    Returns:
        str of AND-prefixed conditions (empty string if none)
    """
    if not filters:
        return ""

    fm = field_map or {}
    conds = []

    simple_fields = ["company", "store", "brand", "item_group", "source",
                     "settlement_type", "inspector", "status"]

    for key in simple_fields:
        val = filters.get(key)
        if val:
            col = fm.get(key, key)
            prefix = f"{alias}" if alias else ""
            conds.append(f"{prefix}{col} = {frappe.db.escape(val)}")

    return (" AND " + " AND ".join(conds)) if conds else ""


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
