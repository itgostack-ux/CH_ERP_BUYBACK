"""CH Data Wipe Certificate — device sanitisation record.

Market context (Cashify, Samsung Exchange, Best Buy Trade-In, Apple Trade In):
Before a bought-back device can enter the refurb / resale pipeline it must be
sanitised so no residual customer data leaves the retailer. This DocType is
the legally-defensible, one-per-device audit trail of the wipe.

Contract:
- One certificate per Buyback Order (plus IMEI when multi-device support is
  added later).
- Must be Submitted before Refurbishment Order can transition to Restocked.
- The `wipe_verified` flag encodes the maker-checker: the person who wiped
  is not automatically trusted; a second reviewer must verify.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class CHDataWipeCertificate(Document):
    def autoname(self):
        # Certificate number == doc name (Frappe autoname handles the series).
        pass

    def validate(self):
        if self.wiped_at and self.wiped_at > now_datetime():
            frappe.throw(_("Wipe timestamp cannot be in the future."))

        if self.wipe_verified:
            if not self.verified_by:
                frappe.throw(_("Verified By is required when Wipe Verified is checked."))
            if not self.verified_at:
                self.verified_at = now_datetime()
            if self.verified_by == self.wiped_by:
                frappe.throw(
                    _(
                        "Wipe verifier must be a different user than the person "
                        "who performed the wipe (maker-checker rule)."
                    ),
                    title=_("Maker-Checker Required"),
                )
        else:
            # Clear verification fields when unchecked to keep the audit trail clean.
            self.verified_by = None
            self.verified_at = None
            self.verification_method = None

        # Set certificate_number to doc name after autoname assigns it
        if self.name and not self.certificate_number:
            self.certificate_number = self.name

    def on_submit(self):
        self.db_set("status", "Verified" if self.wipe_verified else "Submitted")
        # Stamp the parent Buyback Order for quick lookup and gates.
        if self.buyback_order:
            frappe.db.set_value(
                "Buyback Order",
                self.buyback_order,
                {
                    "data_wipe_certificate": self.name,
                    "data_wipe_completed_at": self.wiped_at or now_datetime(),
                },
                update_modified=False,
            )
        # Stamp Serial No when we have one (helps refurb pipeline gates).
        if self.serial_no and frappe.db.exists("Serial No", self.serial_no):
            try:
                frappe.db.set_value(
                    "Serial No",
                    self.serial_no,
                    {"ch_data_wiped": 1},
                    update_modified=False,
                )
            except Exception:
                # Custom field may not exist on all sites yet — non-fatal.
                frappe.logger("buyback").warning(
                    f"Serial No {self.serial_no}: could not set ch_data_wiped"
                )

    def on_cancel(self):
        self.db_set("status", "Revoked")
        # Only clear the parent's link if it currently points to this cert —
        # never clobber a newer certificate.
        if self.buyback_order:
            current = frappe.db.get_value(
                "Buyback Order", self.buyback_order, "data_wipe_certificate"
            )
            if current == self.name:
                frappe.db.set_value(
                    "Buyback Order",
                    self.buyback_order,
                    {
                        "data_wipe_certificate": None,
                        "data_wipe_completed_at": None,
                    },
                    update_modified=False,
                )
        if self.serial_no and frappe.db.exists("Serial No", self.serial_no):
            try:
                frappe.db.set_value(
                    "Serial No", self.serial_no, {"ch_data_wiped": 0},
                    update_modified=False,
                )
            except Exception:
                pass
