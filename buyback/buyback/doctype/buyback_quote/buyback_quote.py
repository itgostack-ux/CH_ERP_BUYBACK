import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate, add_days, getdate

from buyback.exceptions import BuybackStatusError
from buyback.utils import log_audit


class BuybackQuote(Document):
    def before_insert(self):
        """Auto-assign sequential integer ID using advisory lock."""
        frappe.db.sql("SELECT GET_LOCK('buyback_quote_id', 10)")
        try:
            last = frappe.db.sql(
                "SELECT MAX(quote_id) FROM `tabBuyback Quote`"
            )[0][0] or 0
            self.quote_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('buyback_quote_id')")

        if not self.valid_from:
            self.valid_from = nowdate()
        if not self.valid_until:
            validity_days = frappe.db.get_single_value("Buyback Settings", "quote_validity_days") or 7
            self.valid_until = add_days(self.valid_from, validity_days)

        self.status = "Draft"

    def validate(self):
        self._calculate_pricing()

    def _calculate_pricing(self):
        """Calculate estimated price from base price minus deductions."""
        self.estimated_price = max(0, (self.base_price or 0) - (self.total_deductions or 0))
        if not self.quoted_price:
            self.quoted_price = self.estimated_price

    def mark_quoted(self):
        """Transition from Draft to Quoted."""
        if self.status != "Draft":
            frappe.throw(_("Can only quote from Draft status."), exc=BuybackStatusError)
        self.status = "Quoted"
        self.save()
        log_audit("Quote Created", "Buyback Quote", self.name)

    def mark_accepted(self):
        """Customer accepts the quote."""
        if self.status != "Quoted":
            frappe.throw(_("Can only accept a Quoted quote."), exc=BuybackStatusError)
        self.status = "Accepted"
        self.save()
        log_audit("Quote Accepted", "Buyback Quote", self.name)

    def mark_expired(self):
        """Auto-expire or manually expire the quote."""
        if self.status in ("Draft", "Quoted"):
            self.status = "Expired"
            self.save()
            log_audit("Quote Expired", "Buyback Quote", self.name)

    def is_valid(self):
        """Check if quote is still within validity period."""
        if self.status not in ("Quoted", "Accepted"):
            return False
        if self.valid_until and getdate(self.valid_until) < getdate(nowdate()):
            return False
        return True
