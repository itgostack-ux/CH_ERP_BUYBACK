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
        self._sync_workflow_state()

    def validate_workflow(self):
        """Skip Frappe's workflow re-validation — status machine is
        server-managed and workflow_state is a desk-visibility mirror
        (same rationale as Buyback Order.validate_workflow)."""
        return

    def _sync_workflow_state(self):
        """Keep workflow_state aligned when status is changed via server actions."""
        if not self.meta.has_field("workflow_state"):
            return
        if self.status and self.workflow_state != self.status:
            self.workflow_state = self.status

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
            updates = {"status": "New Device Delivered"}
            if self.meta.has_field("workflow_state"):
                updates["workflow_state"] = "New Device Delivered"
            self.db_set(updates, notify=True)
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
        updates = {"status": "Cancelled"}
        if self.meta.has_field("workflow_state"):
            updates["workflow_state"] = "Cancelled"
        self.db_set(updates, notify=True)

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
        # Safety net: the traded-in device is normally moved to the Buyback bin
        # when the exchange invoice is submitted
        # (exchange_hooks.move_traded_device_to_buyback_on_invoice). This call
        # covers exchanges closed without a linked sales invoice; it is
        # idempotent (skips when the device already left the sellable stock).
        self._move_old_device_to_buyback_bin()

    def _move_old_device_to_buyback_bin(self):
        """Exchange invoice completed → move the traded-in device out of the
        reserved sellable stock into the store's Buyback bin for refurbishment.
        Idempotent + best-effort."""
        self._relocate_old_device(
            target_wh_bin_type="Buyback",
            tag_bin_type="Buyback",
            reason=f"Exchange {self.name} invoice completed — to Buyback bin",
            context="move to Buyback bin",
        )

    def _restore_old_device_to_reserved(self):
        """Exchange invoice cancelled → reverse of _move_old_device_to_buyback_bin.
        Bring the traded-in device back out of the Buyback bin into the store's
        SELLABLE warehouse, RESERVED for the original buyback customer — so the
        exchange can be re-invoiced or the device handed back to that same
        customer. Idempotent + best-effort."""
        self._relocate_old_device(
            target_wh_bin_type="Sellable",
            tag_bin_type="Reserved",
            reason=f"Exchange {self.name} invoice cancelled — re-reserved for buyback customer",
            context="restore to reserved sellable stock",
        )

    def _relocate_old_device(self, target_wh_bin_type, tag_bin_type, reason, context):
        """Physically move the traded-in device to the store's
        ``target_wh_bin_type`` warehouse AND tag its logical bin ``tag_bin_type``,
        so POS selling is controlled on both gates it checks (Serial No.warehouse
        and CH Stock Bin.bin_type). Best-effort (must not roll back the caller);
        idempotent — skips when the device left inventory (sold) or is already in
        the target warehouse.
        """
        serial = (self.old_imei_serial or "").strip()
        if not serial or not frappe.db.exists("Serial No", serial):
            return
        sn = frappe.db.get_value(
            "Serial No", serial, ["warehouse", "status", "item_code"], as_dict=True
        )
        if not sn or not sn.warehouse or sn.status != "Active":
            return  # already sold / left inventory — nothing to move

        from buyback.utils import resolve_store_bin_warehouse

        target_wh = resolve_store_bin_warehouse(self.store, self.company, target_wh_bin_type)
        if not target_wh:
            return
        try:
            if target_wh != sn.warehouse:
                se = frappe.get_doc({
                    "doctype": "Stock Entry",
                    "stock_entry_type": "Material Transfer",
                    "company": self.company,
                    "posting_date": frappe.utils.nowdate(),
                    "remarks": reason,
                    "items": [{
                        "item_code": sn.item_code,
                        "s_warehouse": sn.warehouse,
                        "t_warehouse": target_wh,
                        "qty": 1,
                        "serial_no": serial,
                    }],
                })
                se.insert(ignore_permissions=True)
                se.flags.ignore_permissions = True
                # Intra-store bin reclassification (same store's child bins):
                # not an inter-store transfer, so exempt from the in-transit
                # logistics guard (procurement_guardrails).
                se.flags.ignore_procurement_guardrails = True
                se.submit()
            from ch_erp15.ch_erp15.stock_bin_api import move_to_bin
            move_to_bin(
                serial, tag_bin_type,
                reason=reason,
                reference_doctype="Buyback Exchange Order",
                reference_name=self.name,
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Exchange {self.name}: {context} failed for {serial}",
            )
