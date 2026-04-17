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
        # Auto-generate question_code from question_text if not provided
        if not self.question_code and self.question_text:
            import re
            code = self.question_text.strip().lower()
            code = re.sub(r"[^a-z0-9\s_]", "", code)
            code = re.sub(r"\s+", "_", code)[:140]
            self.question_code = code

        if self.question_code:
            self.question_code = self.question_code.strip().lower().replace(" ", "_")

        # Ensure question_code is unique — append suffix if a duplicate exists
        if self.question_code:
            base_code = self.question_code
            existing = frappe.db.get_value(
                "Buyback Question Bank",
                {"question_code": self.question_code, "name": ["!=", self.name]},
                "name",
            )
            if existing:
                suffix = 2
                while frappe.db.exists(
                    "Buyback Question Bank",
                    {"question_code": f"{base_code}_{suffix}", "name": ["!=", self.name]},
                ):
                    suffix += 1
                self.question_code = f"{base_code}_{suffix}"

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
