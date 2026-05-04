import frappe
from frappe.model.document import Document


class BuybackQuestionCategory(Document):
    def validate(self):
        self.category_name = self.category_name.strip() if self.category_name else self.category_name
