import frappe
from frappe.model.document import Document


class BuybackPriceMaster(Document):
    def before_insert(self):
        last = frappe.db.sql("""
            SELECT MAX(buyback_price_id) FROM `tabBuyback Price Master`
        """)[0][0] or 0

        self.buyback_price_id = last + 1
