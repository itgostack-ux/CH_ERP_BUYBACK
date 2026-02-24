# Copyright (c) 2026, Abiraj and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AppoinmentTypeMaster(Document):

    def before_insert(self):
        # get last max id
        last = frappe.db.sql("""
            SELECT MAX(appoinment_id)
            FROM `tabAppoinment Type Master`
        """)[0][0] or 0

        # increment
        self.appoinment_id = last + 1

        # default active (optional)
        if self.is_active is None:
            self.is_active = 1