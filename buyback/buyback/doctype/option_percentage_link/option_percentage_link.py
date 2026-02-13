# Copyright (c) 2026, Abiraj and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class OptionPercentageLink(Document):

    # -------------------------
    # BEFORE INSERT
    # -------------------------
    def before_insert(self):

        last = frappe.db.sql("""
            SELECT MAX(option_percentage_id)
            FROM `tabOption Percentage Link`
            FOR UPDATE
        """)[0][0] or 0

        self.option_percentage_id = last + 1

        # default active
        if self.is_active is None:
            self.is_active = 1

    # -------------------------
    # VALIDATE
    # -------------------------
    def validate(self):

        # validate percentage field (correct name)
        if self.percentage is not None:
            if self.percentage < 0 or self.percentage > 100:
                frappe.throw("Percentage must be between 0 and 100")
