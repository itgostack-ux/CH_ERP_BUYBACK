import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, flt

from buyback.utils import validate_indian_phone

from buyback.exceptions import BuybackStatusError
from buyback.utils import log_audit


class BuybackExchangeOrder(Document):
    def before_insert(self):
        """Auto-assign sequential exchange_id using atomic SQL increment."""
        result = frappe.db.sql(
            "SELECT IFNULL(MAX(exchange_id), 0) + 1 FROM `tabBuyback Exchange Order` FOR UPDATE"
        )
        self.exchange_id = result[0][0] if result else 1
        self.status = "Draft"

    def validate(self):
        if self.mobile_no:
            self.mobile_no = validate_indian_phone(self.mobile_no, "Mobile No")
        self._sync_customer_id()
        self._calculate_amount_to_pay()

    def _sync_customer_id(self):
        """Populate ch_customer_id / ch_membership_id from Customer master."""
        if not self.customer or (self.ch_customer_id and self.ch_membership_id):
            return
        cust = frappe.db.get_value(
            "Customer", self.customer,
            ["ch_customer_id", "ch_membership_id"],
            as_dict=True,
        )
        if cust:
            if not self.ch_customer_id:
                self.ch_customer_id = cust.ch_customer_id
            if not self.ch_membership_id:
                self.ch_membership_id = cust.ch_membership_id

    def on_submit(self):
        if self.status == "Draft":
            self.status = "New Device Delivered"
        log_audit("Exchange Created", "Buyback Exchange Order", self.name,
                  new_value={"buyback_amount": self.buyback_amount,
                             "new_device_price": self.new_device_price,
                             "amount_to_pay": self.amount_to_pay})

        # Sync old device lifecycle to Buyback
        if self.old_imei_serial:
            from buyback.serial_no_utils import sync_exchange_to_lifecycle
            sync_exchange_to_lifecycle(
                self.old_imei_serial,
                exchange_name=self.name,
                buyback_amount=flt(self.buyback_amount),
                customer=self.customer,
            )

    def on_cancel(self):
        self.status = "Cancelled"

    def _calculate_amount_to_pay(self):
        """Calculate: new_device_price - buyback_amount - exchange_discount."""
        self.amount_to_pay = max(0, flt(self.new_device_price) - flt(self.buyback_amount) - flt(self.exchange_discount))

    def deliver_new_device(self):
        """Mark new device as delivered."""
        if self.status != "New Device Delivered":
            frappe.throw(_("Invalid status transition."), exc=BuybackStatusError, title=_("Buyback Exchange Order Error"))
        self.new_device_delivered_at = now_datetime()
        self.status = "Awaiting Pickup"
        self.save()

    def receive_old_device(self):
        """Mark old device as received."""
        if self.status != "Awaiting Pickup":
            frappe.throw(_("Must be in 'Awaiting Pickup' status."), exc=BuybackStatusError, title=_("Buyback Exchange Order Error"))
        self.old_device_received_at = now_datetime()
        self.status = "Old Device Received"
        self.save()

    def inspect_old_device(self, grade=None):
        """Inspect the received old device."""
        if self.status != "Old Device Received":
            frappe.throw(_("Must receive old device before inspection."), exc=BuybackStatusError, title=_("Buyback Exchange Order Error"))
        self.old_device_inspected_at = now_datetime()
        if grade:
            self.old_condition_grade = grade
        self.status = "Inspected"
        self.save()

    def settle(self, reference=None):
        """Settle the exchange."""
        if self.status != "Inspected":
            frappe.throw(_("Must inspect old device before settlement."), exc=BuybackStatusError, title=_("Buyback Exchange Order Error"))
        self.settlement_date = frappe.utils.nowdate()
        if reference:
            self.settlement_reference = reference
        self.status = "Settled"
        self.save()
        log_audit("Settlement Done", "Buyback Exchange Order", self.name)

    def close(self):
        """Close the exchange order."""
        if self.status != "Settled":
            frappe.throw(_("Can only close settled exchange orders."), exc=BuybackStatusError, title=_("Buyback Exchange Order Error"))
        self.status = "Closed"
        self.save()
