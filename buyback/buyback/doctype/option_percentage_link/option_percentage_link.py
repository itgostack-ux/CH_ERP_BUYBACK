import frappe
from frappe.model.document import Document


class OptionPercentageLink(Document):

    def before_insert(self):

        last = frappe.db.sql("""
            SELECT MAX(option_percentage_id) FROM `tabOption Percenge Link`
        """)[0][0] or 0

        self.option_percentage_id = last + 1
