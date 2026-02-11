import frappe
from frappe.model.document import Document


class PaymentMethodMaster(Document):

    def before_insert(self):

        last = frappe.db.sql("""
            SELECT MAX(payment_id) FROM `tabPayment Method Master`
        """)[0][0] or 0

        self.payment_id = last + 1
