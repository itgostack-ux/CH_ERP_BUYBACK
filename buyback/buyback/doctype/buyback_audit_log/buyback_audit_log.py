import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class BuybackAuditLog(Document):
    def before_insert(self):
        """Auto-assign sequential integer ID using advisory lock."""
        frappe.db.sql("SELECT GET_LOCK('buyback_audit_log_id', 10)")
        try:
            last = frappe.db.sql(
                "SELECT MAX(audit_id) FROM `tabBuyback Audit Log`"
            )[0][0] or 0
            self.audit_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('buyback_audit_log_id')")

        if not self.timestamp:
            self.timestamp = now_datetime()
        if not self.user:
            self.user = frappe.session.user
        if not self.ip_address:
            self.ip_address = getattr(frappe.local, "request_ip", None)
