"""Make Buyback Assessment actually submittable, and heal the rows left behind.

``BuybackAssessment.submit_assessment`` used to flip ``status`` to "Submitted"
and ``save()`` — docstatus stayed 0. Two consequences this patch clears:

1. Every operational buyback role was seeded with ``submit = 0`` on the
   Custom DocPerm rows (which override the DocType's own permissions), because
   nothing ever needed it. A real submit — and every later
   ``update_after_submit`` write such as ``create_inspection`` — needs it.
2. Assessments already at status "Submitted" / "Inspection Created" still sit
   at docstatus 0, so the Desk form and list views badge them red "Draft".
   They are logically submitted; stamp the docstatus to match. Cannot go
   through the ORM (docstatus 0 → 1 via save is a submit, which would re-run
   validate against masters that may have moved since), so set the column.
"""

from __future__ import annotations

import frappe

DOCTYPE = "Buyback Assessment"

SUBMIT_ROLES = (
    "Buyback Agent",
    "Buyback Store Manager",
    "Buyback Manager",
    "Buyback Admin",
)
CANCEL_ROLES = (
    "Buyback Store Manager",
    "Buyback Manager",
    "Buyback Admin",
)

# Statuses that only exist on the far side of submit_assessment().
SUBMITTED_STATUSES = ("Submitted", "Inspection Created")


def execute():
    if not frappe.db.table_exists(DOCTYPE):
        return

    _grant_submit_permissions()
    _stamp_docstatus_on_submitted_rows()


def _grant_submit_permissions():
    from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype
    from frappe.permissions import setup_custom_perms

    if not frappe.db.exists("Custom DocPerm", {"parent": DOCTYPE}):
        # No overrides in play — the DocType JSON alone decides, and it now
        # carries submit. Nothing to heal.
        return

    setup_custom_perms(DOCTYPE)
    changed = False
    for role in SUBMIT_ROLES:
        name = frappe.db.get_value(
            "Custom DocPerm",
            {"parent": DOCTYPE, "role": role, "permlevel": 0, "if_owner": 0},
            "name",
        )
        if not name:
            continue
        updates = {}
        if not frappe.db.get_value("Custom DocPerm", name, "submit"):
            updates["submit"] = 1
        if role in CANCEL_ROLES:
            for ptype in ("cancel", "amend"):
                if not frappe.db.get_value("Custom DocPerm", name, ptype):
                    updates[ptype] = 1
        if updates:
            frappe.db.set_value("Custom DocPerm", name, updates, update_modified=False)
            changed = True

    if changed:
        validate_permissions_for_doctype(DOCTYPE)
        frappe.clear_cache(doctype=DOCTYPE)


def _stamp_docstatus_on_submitted_rows():
    stale = frappe.get_all(
        DOCTYPE,
        filters={"docstatus": 0, "status": ["in", SUBMITTED_STATUSES]},
        pluck="name",
        limit_page_length=0,
    )
    if not stale:
        return
    frappe.db.sql(
        f"""
        UPDATE `tab{DOCTYPE}`
        SET docstatus = 1
        WHERE docstatus = 0 AND status IN %(statuses)s
        """,
        {"statuses": SUBMITTED_STATUSES},
    )
    for child in frappe.get_meta(DOCTYPE).get_table_fields():
        frappe.db.sql(
            f"""
            UPDATE `tab{child.options}`
            SET docstatus = 1
            WHERE parenttype = %(parenttype)s AND parent IN %(parents)s
            """,
            {"parenttype": DOCTYPE, "parents": stale},
        )
    frappe.logger("buyback").info(
        f"Stamped docstatus=1 on {len(stale)} already-submitted Buyback Assessments"
    )
