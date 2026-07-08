"""CH Buyback Pickup Appointment — customer-side pickup scheduling record.

Market context (Cashify field-executive pickup, Samsung authorized pickup,
Flipkart pickup partners, Amazon exchange pickup): every pickup order is
represented by an attempt sequence. A customer gets three attempts before the
order lifecycle is escalated / auto-cancelled — this is the compliance-safe
default across major players.

Each appointment is a single dated attempt. Rescheduling creates a new
appointment (attempt N+1) and links the prior via `reschedule_to`. Cancelling
never re-uses the same appointment record.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

MAX_ATTEMPTS = 3


class CHBuybackPickupAppointment(Document):
    def autoname(self):
        pass

    def validate(self):
        self._auto_set_attempt_number()
        self._auto_set_appointment_id()
        self._enforce_status_semantics()

    def _auto_set_attempt_number(self):
        if self.attempt_number and self.docstatus != 0:
            return
        # Count prior non-cancelled attempts for this order (excluding self).
        count = frappe.db.count(
            "CH Buyback Pickup Appointment",
            filters={
                "buyback_order": self.buyback_order,
                "docstatus": ["<", 2],
                "name": ["!=", self.name or ""],
            },
        )
        self.attempt_number = count + 1
        if self.attempt_number > MAX_ATTEMPTS:
            frappe.msgprint(
                _(
                    "This is attempt #{0} for Buyback Order {1} — the market-"
                    "standard cap is {2}. Consider cancelling the order or "
                    "raising an exception instead of scheduling another attempt."
                ).format(self.attempt_number, self.buyback_order, MAX_ATTEMPTS),
                title=_("Attempt Cap Exceeded"),
                indicator="orange",
            )

    def _auto_set_appointment_id(self):
        if self.name and not self.appointment_id:
            self.appointment_id = self.name

    def _enforce_status_semantics(self):
        if self.status == "Completed" and not self.completed_at:
            self.completed_at = now_datetime()
        if self.status == "Cancelled" and not self.cancelled_at:
            self.cancelled_at = now_datetime()
        if self.status == "Attempted (Failed)" and not self.failure_reason:
            frappe.throw(
                _("Failure Reason is required when marking the attempt Failed."),
                title=_("Failure Reason Missing"),
            )

    def on_submit(self):
        self._stamp_buyback_order()

    def on_update_after_submit(self):
        # Status changes after submit (Confirmed → En Route → Completed) must
        # be reflected on the parent Buyback Order for hub / dashboard views.
        self._stamp_buyback_order()

    def _stamp_buyback_order(self):
        if not self.buyback_order:
            return

        latest_completed_at = None
        if self.status == "Completed":
            latest_completed_at = self.completed_at or now_datetime()

        payload = {
            "latest_pickup_appointment": self.name,
            "pickup_attempts_count": self.attempt_number,
        }
        if latest_completed_at:
            payload["pickup_completed_at"] = latest_completed_at

        try:
            frappe.db.set_value(
                "Buyback Order", self.buyback_order, payload, update_modified=False
            )
        except Exception:
            frappe.logger("buyback").warning(
                f"Could not stamp Buyback Order {self.buyback_order} from pickup appointment {self.name}"
            )

    def on_cancel(self):
        self.db_set("status", "Cancelled")
        self.db_set("cancelled_at", now_datetime())
