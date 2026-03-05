import frappe
from frappe.model.document import Document


class BuybackChecklistTemplate(Document):
    def before_insert(self):
        """Auto-assign sequential integer ID using advisory lock."""
        frappe.db.sql("SELECT GET_LOCK('buyback_checklist_template_id', 10)")
        try:
            last = frappe.db.sql(
                "SELECT MAX(checklist_id) FROM `tabBuyback Checklist Template`"
            )[0][0] or 0
            self.checklist_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('buyback_checklist_template_id')")

    def validate(self):
        # Validate check_codes are unique within template
        if self.items:
            codes = [item.check_code for item in self.items]
            if len(codes) != len(set(codes)):
                frappe.throw(
                    frappe._("Check codes must be unique within a template."),
                    title=frappe._("Duplicate Check Codes"),
                )
