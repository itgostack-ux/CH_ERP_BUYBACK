import frappe


def execute():
    """Backfill multiselect categories from legacy single-category field.

    Idempotent:
    - Skips when target child table/docfield is unavailable.
    - Skips rows already present in the child table.
    """
    if not frappe.db.table_exists("tabBuyback Question Bank"):
        return
    if not frappe.db.table_exists("tabBuyback Question Applicable Category"):
        return

    rows = frappe.get_all(
        "Buyback Question Bank",
        filters={"applies_to_category": ["not in", ["", None]]},
        fields=["name", "applies_to_category"],
        limit_page_length=0,
    )

    for row in rows:
        if not row.applies_to_category:
            continue

        exists = frappe.db.exists(
            "Buyback Question Applicable Category",
            {
                "parent": row.name,
                "parenttype": "Buyback Question Bank",
                "parentfield": "applies_to_categories",
                "item_group": row.applies_to_category,
            },
        )
        if exists:
            continue

        frappe.get_doc(
            {
                "doctype": "Buyback Question Applicable Category",
                "parent": row.name,
                "parenttype": "Buyback Question Bank",
                "parentfield": "applies_to_categories",
                "item_group": row.applies_to_category,
            }
        ).insert(ignore_permissions=True)
