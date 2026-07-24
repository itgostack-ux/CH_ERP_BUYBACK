import frappe
from frappe.model.document import Document

from buyback.utils import next_numeric_external_id


class BuybackChecklistTemplate(Document):
    def before_insert(self):
        self.checklist_id = next_numeric_external_id(
            "Buyback Checklist Template", "checklist_id"
        )

    def validate(self):
        # Validate check_codes are unique within template
        if self.items:
            codes = [item.check_code for item in self.items]
            if len(codes) != len(set(codes)):
                frappe.throw(
                    frappe._("Check codes must be unique within a template."),
                    title=frappe._("Duplicate Check Codes"),
                )
