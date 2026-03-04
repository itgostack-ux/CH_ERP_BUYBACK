import frappe
from frappe.model.document import Document


class OptionMaster(Document):

    def before_insert(self):
        frappe.db.sql("SELECT GET_LOCK('option_master_id', 10)")
        try:
            last = frappe.db.sql("""
                SELECT MAX(option_id) FROM `tabOption Master`
            """)[0][0] or 0
            self.option_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('option_master_id')")

        if self.is_active is None:
            self.is_active = 1
