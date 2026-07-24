import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from buyback.utils import next_numeric_external_id


class BuybackAuditLog(Document):
    def before_insert(self):
        self.audit_id = next_numeric_external_id("Buyback Audit Log", "audit_id")

        if not self.timestamp:
            self.timestamp = now_datetime()
        if not self.user:
            self.user = frappe.session.user
        if not self.ip_address:
            self.ip_address = getattr(frappe.local, "request_ip", None)
