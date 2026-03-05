import frappe
from frappe.model.document import Document


class BuybackQuestionBank(Document):
    def before_insert(self):
        """Auto-assign sequential integer ID using advisory lock."""
        frappe.db.sql("SELECT GET_LOCK('buyback_question_bank_id', 10)")
        try:
            last = frappe.db.sql(
                "SELECT MAX(question_id) FROM `tabBuyback Question Bank`"
            )[0][0] or 0
            self.question_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('buyback_question_bank_id')")

    def validate(self):
        if self.question_code:
            self.question_code = self.question_code.strip().lower().replace(" ", "_")

        # Yes/No questions should have exactly 2 options
        if self.question_type == "Yes/No" and self.options:
            if len(self.options) != 2:
                frappe.msgprint(
                    frappe._("Yes/No questions typically have exactly 2 options."),
                    indicator="orange",
                )

        # Validate option values are unique within the question
        if self.options:
            values = [o.option_value for o in self.options]
            if len(values) != len(set(values)):
                frappe.throw(
                    frappe._("Option values must be unique within a question."),
                    title=frappe._("Duplicate Option Values"),
                )
