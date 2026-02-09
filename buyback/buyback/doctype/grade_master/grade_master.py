import frappe
from frappe.model.document import Document


class GradeMaster(Document):

    def before_insert(self):

        last = frappe.db.sql("""
            SELECT MAX(grade_id) FROM `tabGrade Master`
        """)[0][0] or 0

        self.grade_id = last + 1
