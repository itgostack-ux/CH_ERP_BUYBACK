import frappe
from frappe.model.document import Document


class TestMaster(Document):

    def before_insert(self):

        last = frappe.db.sql("""
            SELECT MAX(test_id) FROM `tabTest Master`
        """)[0][0] or 0

        self.test_id = last + 1

        # default active
        if self.is_active is None:
            self.is_active = 1
