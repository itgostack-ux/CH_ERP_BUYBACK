# Copyright (c) 2026, Abiraj and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AppoinmentTypeMaster(Document):

    def before_insert(self):
        frappe.db.sql("SELECT GET_LOCK('appoinment_type_master_id', 10)")
        try:
            last = frappe.db.sql("""
                SELECT MAX(appoinment_id)
                FROM `tabAppoinment Type Master`
            """)[0][0] or 0
            self.appoinment_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('appoinment_type_master_id')")

        if self.is_active is None:
            self.is_active = 1