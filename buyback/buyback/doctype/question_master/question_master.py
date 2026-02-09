import frappe
from frappe.model.document import Document


class QuestionMaster(Document):
    def before_insert(self):

        last = frappe.db.sql("""
            SELECT MAX(question_id) FROM `tabQuestion Master`
        """)[0][0] or 0

        self.question_id = last + 1
