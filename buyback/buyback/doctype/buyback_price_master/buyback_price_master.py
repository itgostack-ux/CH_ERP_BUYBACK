import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from buyback.utils import next_numeric_external_id


# Fields that can only be written via the CH Price Upload Batch (maker/checker)
_PRICE_FIELDS = [
    "current_market_price", "vendor_price",
    # IW 0-3
    "a_grade_iw_0_3", "b_grade_iw_0_3", "c_grade_iw_0_3",
    "scrap_iw_0_3", "phone_dead_iw_0_3",
    # IW 4-6
    "a_grade_iw_0_6", "b_grade_iw_0_6", "c_grade_iw_0_6", "d_grade_iw_0_6",
    "scrap_iw_0_6", "phone_dead_iw_0_6",
    # IW 6-11
    "a_grade_iw_6_11", "b_grade_iw_6_11", "c_grade_iw_6_11", "d_grade_iw_6_11",
    "scrap_iw_6_11", "phone_dead_iw_6_11",
    # OOW 11+
    "a_grade_oow_11", "b_grade_oow_11", "c_grade_oow_11", "d_grade_oow_11",
    "scrap_oow_11", "phone_dead_oow_11",
]


class BuybackPriceMaster(Document):
    def before_insert(self):
        self.buyback_price_id = next_numeric_external_id(
            "Buyback Price Master", "buyback_price_id"
        )
        self.sku_id = self.buyback_price_id

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

    def on_update(self):
        """BB-5 fix: Log price revision audit trail when prices change."""
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return

        changes = []
        for f in _PRICE_FIELDS:
            old_val = flt(doc_before.get(f))
            new_val = flt(self.get(f))
            if old_val != new_val:
                changes.append(f"<b>{f}</b>: {old_val} → {new_val}")

        if changes:
            source = "Ready Reckoner" if self.flags.from_ready_reckoner else "Price Batch"
            comment = (
                f"<b>Price Revision</b> via {source} by {frappe.session.user}<br>"
                + "<br>".join(changes)
            )
            self.add_comment("Info", comment)
