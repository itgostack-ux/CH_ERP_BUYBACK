"""
Recipe: upsert a Grade Master row.

Grade Master is one of the smallest Buyback masters — a single reqd
Data field (grade_name, unique) plus description + display_order. It
is used as the pilot for the E2E harness because it exercises the full
insert cycle (naming series, unique constraint, rollback) without
depending on any other master.

Bench-execute usage::

    bench --site test.localhost execute \\
        buyback.recipes.upsert_grade.run \\
        --kwargs '{"grade_name":"A","display_order":1,"description":"Excellent"}'

Returns
-------
``dict``: ``{"name": <autoname>, "grade_name": <name>, "created": <bool>}``
"""

from __future__ import annotations

import frappe


def run(
    grade_name: str,
    display_order: int = 0,
    description: str | None = None,
) -> dict:
    """Upsert a Grade Master row by grade_name.

    Idempotent: if a row with the same grade_name already exists, its
    display_order + description are updated (if provided) and the
    existing name is returned. ``created`` is True only when a new row
    was inserted.
    """
    if not grade_name:
        raise ValueError("grade_name is required")

    existing = frappe.db.get_value("Grade Master", {"grade_name": grade_name}, "name")
    if existing:
        updates: dict = {}
        if display_order:
            updates["display_order"] = display_order
        if description is not None:
            updates["description"] = description
        if updates:
            frappe.db.set_value("Grade Master", existing, updates)
        return {"name": existing, "grade_name": grade_name, "created": False}

    doc = frappe.get_doc({
        "doctype": "Grade Master",
        "grade_name": grade_name,
        "display_order": display_order,
        "description": description or "",
    }).insert()

    return {"name": doc.name, "grade_name": grade_name, "created": True}
