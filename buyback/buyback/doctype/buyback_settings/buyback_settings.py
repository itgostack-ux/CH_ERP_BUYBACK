import frappe
from frappe.model.document import Document


class BuybackSettings(Document):
    def validate(self):
        # Skip validation during install — settings may not be fully configured yet
        # Pattern: India Compliance guards Settings.validate() with this flag
        if frappe.flags.in_install:
            return
