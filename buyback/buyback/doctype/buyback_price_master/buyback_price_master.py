import frappe
from frappe.model.document import Document


class BuybackPriceMaster(Document):
    def before_insert(self):
        # generate auto SKU id
        self.sku_id = frappe.db.get_next_sequence_val("buyback_price_master_seq")
