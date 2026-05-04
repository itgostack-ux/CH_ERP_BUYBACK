"""
Backfill question_category = 'General' for all Buyback Question Bank
records that have NULL (created before the Category field was added).
"""
import frappe


def execute():
    frappe.db.sql("""
        UPDATE `tabBuyback Question Bank`
        SET question_category = 'General'
        WHERE question_category IS NULL OR question_category = ''
    """)
    frappe.db.commit()
