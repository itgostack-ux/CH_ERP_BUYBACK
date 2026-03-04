import frappe
from frappe.model.document import Document


class BuybackPriceMaster(Document):
    def before_insert(self):
        frappe.db.sql("SELECT GET_LOCK('buyback_price_master_id', 10)")
        try:
            last = frappe.db.sql("""
                SELECT MAX(buyback_price_id) FROM `tabBuyback Price Master`
            """)[0][0] or 0
            self.buyback_price_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('buyback_price_master_id')")
