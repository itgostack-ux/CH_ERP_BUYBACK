# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class BuybackSLALog(Document):
    def before_insert(self):
        if self.actual_minutes and self.expected_minutes:
            self.exceeded_by = round(self.actual_minutes - self.expected_minutes, 1)
        if self.breached:
            self.status = "Breach"
