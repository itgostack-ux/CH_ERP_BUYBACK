import frappe
from frappe.model.document import Document


class StateMaster(Document):

    def before_insert(self):
        last = frappe.db.sql("""
            SELECT MAX(state_id) FROM `tabState Master`
        """)[0][0] or 0

        self.state_id = last + 1
