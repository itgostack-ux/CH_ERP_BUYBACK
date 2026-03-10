import frappe
from frappe.model.document import Document


class BuybackItemQuestionMap(Document):
    def validate(self):
        self._validate_unique_mapping()
        self._validate_has_entries()
        self._validate_no_duplicate_questions()
        self._validate_no_duplicate_tests()

    def _validate_unique_mapping(self):
        """Ensure only one active map per item or item group."""
        filters = {
            "map_type": self.map_type,
            "disabled": 0,
            "name": ("!=", self.name),
        }
        if self.map_type == "Model":
            if not self.item_code:
                frappe.throw("Model (Item) is required when Map Type is Model.")
            filters["item_code"] = self.item_code
        else:
            if not self.item_group:
                frappe.throw("Subcategory (Item Group) is required when Map Type is Subcategory.")
            filters["item_group"] = self.item_group

        existing = frappe.db.exists("Buyback Item Question Map", filters)
        if existing:
            target = self.item_code if self.map_type == "Model" else self.item_group
            frappe.throw(
                f"An active mapping already exists for {self.map_type} '{target}': {existing}. "
                "Disable the existing one first, or edit it."
            )

    def _validate_has_entries(self):
        """At least one question or test must be mapped."""
        if not self.questions and not self.tests:
            frappe.throw("Add at least one Customer Question or Automated Test.")

    def _validate_no_duplicate_questions(self):
        """No duplicate questions within the questions table."""
        seen = set()
        for row in self.questions:
            if row.question in seen:
                frappe.throw(
                    f"Question '{row.question_text or row.question}' is added more than once. "
                    "Remove the duplicate row."
                )
            seen.add(row.question)

    def _validate_no_duplicate_tests(self):
        """No duplicate tests within the tests table."""
        seen = set()
        for row in self.tests:
            if row.test in seen:
                frappe.throw(
                    f"Test '{row.test_name or row.test}' is added more than once. "
                    "Remove the duplicate row."
                )
            seen.add(row.test)
