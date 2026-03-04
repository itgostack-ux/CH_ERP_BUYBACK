import frappe
from frappe.model.document import Document


class GradeMaster(Document):

    def before_insert(self):
        frappe.db.sql("SELECT GET_LOCK('grade_master_id', 10)")
        try:
            last = frappe.db.sql("""
                SELECT MAX(grade_id) FROM `tabGrade Master`
            """)[0][0] or 0
            self.grade_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('grade_master_id')")
