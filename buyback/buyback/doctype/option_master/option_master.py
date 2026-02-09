import frappe
from frappe.model.document import Document


class OptionMaster(Document):

    def before_insert(self):

        last = frappe.db.sql("""
            SELECT MAX(option_id) FROM `tabOption Master`
        """)[0][0] or 0

        self.option_id = last + 1

        # default active
        if self.is_active is None:
            self.is_active = 1
