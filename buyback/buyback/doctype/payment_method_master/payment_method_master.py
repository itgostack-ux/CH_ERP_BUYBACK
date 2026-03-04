import frappe
from frappe.model.document import Document


class PaymentMethodMaster(Document):

    def before_insert(self):
        frappe.db.sql("SELECT GET_LOCK('payment_method_master_id', 10)")
        try:
            last = frappe.db.sql("""
                SELECT MAX(payment_id) FROM `tabPayment Method Master`
            """)[0][0] or 0
            self.payment_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('payment_method_master_id')")
