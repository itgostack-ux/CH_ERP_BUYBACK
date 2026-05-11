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

        self._sync_applies_to_categories()
        self._ensure_unique_question_code()

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

    def _sync_applies_to_categories(self):
        """Keep legacy single-category field in sync with multiselect values."""
        selected = []
        seen = set()
        for row in self.get("applies_to_categories") or []:
            item_group = (row.item_group or "").strip()
            if not item_group or item_group in seen:
                continue
            seen.add(item_group)
            selected.append(item_group)

        # Preserve compatibility with legacy code/data until all callers are migrated.
        if selected:
            self.applies_to_category = selected[0]
        elif self.applies_to_category:
            self.append("applies_to_categories", {"item_group": self.applies_to_category})

    def _ensure_unique_question_code(self):
        """Ensure question_code uniqueness under advisory lock to prevent races."""
        if not self.question_code:
            return

        frappe.db.sql("SELECT GET_LOCK('buyback_question_code', 10)")
        try:
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
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('buyback_question_code')")
