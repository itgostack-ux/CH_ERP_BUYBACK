# Copyright (c) 2026, Abiraj and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class DeviceServices(Document):

    def before_insert(self):
        # set next device_service_id safely
        if not self.device_service_id:
            self.device_service_id = frappe.db.sql("""
                SELECT COALESCE(MAX(device_service_id), 0) + 1
                FROM `tabDevice Services`
                FOR UPDATE
            """)[0][0]