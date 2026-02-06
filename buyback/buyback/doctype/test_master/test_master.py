import frappe
from frappe.model.document import Document

class TestMaster(Document):
    def before_insert(self):
        self.test_id = frappe.db.get_next_sequence_val("test_master_seq")
