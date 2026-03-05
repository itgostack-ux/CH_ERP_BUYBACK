import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, flt

from buyback.exceptions import BuybackStatusError
from buyback.utils import log_audit


class BuybackExchangeOrder(Document):
    def before_insert(self):
        """Auto-assign sequential integer ID using advisory lock."""
        frappe.db.sql("SELECT GET_LOCK('buyback_exchange_order_id', 10)")
        try:
            last = frappe.db.sql(
                "SELECT MAX(exchange_id) FROM `tabBuyback Exchange Order`"
            )[0][0] or 0
            self.exchange_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('buyback_exchange_order_id')")

        self.status = "Draft"

    def validate(self):
        self._calculate_amount_to_pay()

    def on_submit(self):
        if self.status == "Draft":
            self.status = "New Device Delivered"
        log_audit("Exchange Created", "Buyback Exchange Order", self.name,
                  new_value={"buyback_amount": self.buyback_amount,
                             "new_device_price": self.new_device_price,
                             "amount_to_pay": self.amount_to_pay})

    def on_cancel(self):
        self.status = "Cancelled"

    def _calculate_amount_to_pay(self):
        """Calculate: new_device_price - buyback_amount - exchange_discount."""
        self.amount_to_pay = max(0, flt(self.new_device_price) - flt(self.buyback_amount) - flt(self.exchange_discount))

    def deliver_new_device(self):
        """Mark new device as delivered."""
        if self.status != "New Device Delivered":
            frappe.throw(_("Invalid status transition."), exc=BuybackStatusError)
        self.new_device_delivered_at = now_datetime()
        self.status = "Awaiting Pickup"
        self.save()

    def receive_old_device(self):
        """Mark old device as received."""
        if self.status != "Awaiting Pickup":
            frappe.throw(_("Must be in 'Awaiting Pickup' status."), exc=BuybackStatusError)
        self.old_device_received_at = now_datetime()
        self.status = "Old Device Received"
        self.save()

    def inspect_old_device(self, grade=None):
        """Inspect the received old device."""
        if self.status != "Old Device Received":
            frappe.throw(_("Must receive old device before inspection."), exc=BuybackStatusError)
        self.old_device_inspected_at = now_datetime()
        if grade:
            self.old_condition_grade = grade
        self.status = "Inspected"
        self.save()

    def settle(self, reference=None):
        """Settle the exchange."""
        if self.status != "Inspected":
            frappe.throw(_("Must inspect old device before settlement."), exc=BuybackStatusError)
        self.settlement_date = frappe.utils.nowdate()
        if reference:
            self.settlement_reference = reference
        self.status = "Settled"
        self.save()
        log_audit("Settlement Done", "Buyback Exchange Order", self.name)

    def close(self):
        """Close the exchange order."""
        if self.status != "Settled":
            frappe.throw(_("Can only close settled exchange orders."), exc=BuybackStatusError)
        self.status = "Closed"
        self.save()
