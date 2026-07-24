import secrets

import frappe
from frappe.utils import cint
from frappe.model.document import Document

from buyback.utils import get_buyback_setting_value, next_numeric_external_id


class BuybackQuestionBank(Document):
    def before_insert(self):
        self.question_id = next_numeric_external_id(
            "Buyback Question Bank", "question_id"
        )

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

        lock_timeout = max(
            1,
            min(
                cint(get_buyback_setting_value("question_code_lock_timeout_seconds", 10))
                or 10,
                60,
            ),
        )
        lock_result = frappe.db.sql(
            "SELECT GET_LOCK('buyback_question_code', %s)", (lock_timeout,)
        )
        if not lock_result or cint(lock_result[0][0]) != 1:
            frappe.throw(
                frappe._("Unable to reserve a question code. Please retry."),
                frappe.ValidationError,
            )
        try:
            base_code = self.question_code
            existing = frappe.db.get_value(
                "Buyback Question Bank",
                {"question_code": self.question_code, "name": ["!=", self.name]},
                "name",
            )
            if existing:
                retry_limit = max(
                    1,
                    min(
                        cint(get_buyback_setting_value("question_code_suffix_retry_limit", 100))
                        or 100,
                        1000,
                    ),
                )
                candidates = []
                for suffix in range(2, retry_limit + 2):
                    suffix_text = str(suffix)
                    candidates.append(f"{base_code[:139 - len(suffix_text)]}_{suffix_text}")
                occupied = set(
                    frappe.get_all(
                        "Buyback Question Bank",
                        filters={
                            "question_code": ("in", candidates),
                            "name": ("!=", self.name),
                        },
                        pluck="question_code",
                        limit_page_length=retry_limit + 1,
                    )
                )
                self.question_code = next(
                    (candidate for candidate in candidates if candidate not in occupied),
                    "",
                )
                if not self.question_code:
                    entropy = secrets.token_hex(8)
                    fallback = f"{base_code[:139 - len(entropy)]}_{entropy}"
                    if frappe.db.exists(
                        "Buyback Question Bank",
                        {"question_code": fallback, "name": ("!=", self.name)},
                    ):
                        frappe.throw(
                            frappe._("Unable to allocate a unique question code. Please retry."),
                            frappe.ValidationError,
                        )
                    self.question_code = fallback
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('buyback_question_code')")
