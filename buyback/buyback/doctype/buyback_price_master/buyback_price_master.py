import frappe
from frappe import _
from frappe.model.document import Document


# Fields that can only be written via the CH Price Upload Batch (maker/checker)
_PRICE_FIELDS = [
    "current_market_price", "vendor_price",
    "a_grade_iw_0_3", "b_grade_iw_0_3", "c_grade_iw_0_3",
    "a_grade_iw_0_6", "b_grade_iw_0_6", "c_grade_iw_0_6", "d_grade_iw_0_6",
    "a_grade_iw_6_11", "b_grade_iw_6_11", "c_grade_iw_6_11", "d_grade_iw_6_11",
    "a_grade_oow_11", "b_grade_oow_11", "c_grade_oow_11", "d_grade_oow_11",
]


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

    def _is_programmatic_price_update(self):
        """Allow price writes from approved CH Price Upload Batch or Ready Reckoner."""
        return self.flags.from_price_batch or self.flags.from_ready_reckoner

    def validate(self):
        """Block direct price edits — prices flow through maker/checker batch approval."""
        if self._is_programmatic_price_update():
            return

        if self.is_new():
            for f in _PRICE_FIELDS:
                if self.get(f):
                    frappe.throw(
                        _("Buyback prices can only be managed via the CH Ready Reckoner "
                          "(maker/checker approval). Direct edits are not allowed."),
                        title=_("Price Edit Not Allowed"),
                    )
        else:
            doc_before = self.get_doc_before_save()
            if doc_before:
                for f in _PRICE_FIELDS:
                    if (self.get(f) or 0) != (doc_before.get(f) or 0):
                        frappe.throw(
                            _("Buyback prices can only be changed via the CH Ready Reckoner "
                              "(maker/checker approval). Direct edits are not allowed."),
                            title=_("Price Edit Not Allowed"),
                        )
