"""
Patch: Split Item Question Map into separate question/test tables.

Moves rows with diagnosis_type='Automated Test' from the old `questions`
child table to the new `tests` child table (Buyback Item Test Map Detail).
"""

import frappe


def execute():
    if not frappe.db.table_exists("tabBuyback Item Test Map Detail"):
        return
    if not frappe.db.has_column("Buyback Item Question Map Detail", "diagnosis_type"):
        return

    # Find rows that were Automated Tests in the old mixed table
    test_rows = frappe.db.sql(
        """
        SELECT parent, question, question_text, question_code, display_order, idx
        FROM `tabBuyback Item Question Map Detail`
        WHERE diagnosis_type = 'Automated Test'
        ORDER BY parent, display_order, idx
        """,
        as_dict=True,
    )

    for i, row in enumerate(test_rows, 1):
        frappe.get_doc({
            "doctype": "Buyback Item Test Map Detail",
            "parent": row.parent,
            "parenttype": "Buyback Item Question Map",
            "parentfield": "tests",
            "idx": i,
            "test": row.question,
            "test_name": row.question_text,
            "test_code": row.question_code,
            "display_order": row.display_order,
        }).db_insert()

    # Delete migrated rows from old table
    if test_rows:
        frappe.db.sql(
            """
            DELETE FROM `tabBuyback Item Question Map Detail`
            WHERE diagnosis_type = 'Automated Test'
            """
        )

    frappe.db.commit()
