import frappe
from frappe.model.document import Document


class QuestionMaster(Document):
    def before_insert(self):
        frappe.db.sql("SELECT GET_LOCK('question_master_id', 10)")
        try:
            last = frappe.db.sql("""
                SELECT MAX(question_id) FROM `tabQuestion Master`
            """)[0][0] or 0
            self.question_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('question_master_id')")
