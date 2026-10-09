"""Assessments the mobile app wrote with the grade's letter instead of its
Grade Master: point them at the master, so they can be saved and submitted.

New ones are corrected as they are touched (BuybackAssessment.validate); this
puts right the ones already there, including those nobody has opened yet.
"""
import frappe


def execute():
    if not frappe.db.has_column("Grade Master", "grade_name"):
        return
    frappe.db.sql(
        """UPDATE `tabBuyback Assessment` a
             JOIN `tabGrade Master` g ON g.grade_name = a.estimated_grade
        LEFT JOIN `tabGrade Master` own ON own.name = a.estimated_grade
              SET a.estimated_grade = g.name
            WHERE own.name IS NULL AND IFNULL(a.estimated_grade, '') != ''"""
    )
