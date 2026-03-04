import frappe
from frappe.model.document import Document


class TestMaster(Document):

    def before_insert(self):
        frappe.db.sql("SELECT GET_LOCK('test_master_id', 10)")
        try:
            last = frappe.db.sql("""
                SELECT MAX(test_id) FROM `tabTest Master`
            """)[0][0] or 0
            self.test_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('test_master_id')")

        if self.is_active is None:
            self.is_active = 1
