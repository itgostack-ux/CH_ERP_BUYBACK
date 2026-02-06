import frappe
from frappe.model.document import Document


class BuybackPriceMaster(Document):
    def before_insert(self):
        last = frappe.db.sql("""
            SELECT MAX(sku_id) FROM `tabBuyback Price Master`
        """)[0][0] or 0

        self.sku_id = last + 1
