import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, flt

from buyback.exceptions import BuybackStatusError
from buyback.utils import log_audit


class BuybackOrder(Document):
    def before_insert(self):
        """Auto-assign sequential integer ID using advisory lock."""
        frappe.db.sql("SELECT GET_LOCK('buyback_order_id', 10)")
        try:
            last = frappe.db.sql(
                "SELECT MAX(order_id) FROM `tabBuyback Order`"
            )[0][0] or 0
            self.order_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('buyback_order_id')")

        self.status = "Draft"

    def validate(self):
        self._calculate_payment_totals()
        self._check_approval_requirement()

    def on_submit(self):
        """Create accounting entries on submit (atomic — rollback both on failure)."""
        try:
            self._create_journal_entry()
            self._create_stock_entry()
        except Exception:
            frappe.log_error(
                title=_("Buyback Order {0}: JE/SE creation failed").format(self.name),
            )
            raise

        if self.status == "Draft":
            self.status = "Awaiting Approval" if self.requires_approval else "Approved"
        log_audit("Order Created", "Buyback Order", self.name,
                  new_value={"final_price": self.final_price, "status": self.status})

    def on_cancel(self):
        """Reverse GL and Stock entries on cancellation."""
        self._cancel_linked_entry("Journal Entry", self.journal_entry)
        self._cancel_linked_entry("Stock Entry", self.stock_entry)
        self.status = "Cancelled"
        log_audit("Order Cancelled", "Buyback Order", self.name,
                  new_value={"status": "Cancelled"})

    def _cancel_linked_entry(self, doctype, name):
        """Cancel a linked JE or SE if it exists and is submitted."""
        if not name:
            return
        try:
            doc = frappe.get_doc(doctype, name)
            if doc.docstatus == 1:
                doc.cancel()
        except Exception:
            frappe.log_error(
                f"Failed to cancel {doctype} {name} for {self.name}",
                "Buyback Order Cancel",
            )

    def _calculate_payment_totals(self):
        """Sum up all payment rows."""
        self.total_paid = sum(flt(p.amount) for p in (self.payments or []))
        if self.total_paid == 0:
            self.payment_status = "Unpaid"
        elif self.total_paid < flt(self.final_price):
            self.payment_status = "Partially Paid"
        elif self.total_paid == flt(self.final_price):
            self.payment_status = "Paid"
        else:
            self.payment_status = "Overpaid"

    def _check_approval_requirement(self):
        """Set requires_approval flag based on settings."""
        threshold = frappe.db.get_single_value(
            "Buyback Settings", "require_manager_approval_above"
        ) or 0
        if flt(self.final_price) > flt(threshold):
            self.requires_approval = 1

    def approve(self, remarks=None):
        """Manager approves the order."""
        if self.status != "Awaiting Approval":
            frappe.throw(
                _("Can only approve orders in 'Awaiting Approval' status."),
                exc=BuybackStatusError,
            )
        self.status = "Approved"
        self.approved_by = frappe.session.user
        self.approved_price = self.final_price
        self.approval_date = now_datetime()
        if remarks:
            self.approval_remarks = remarks
        self.save()
        log_audit("Order Approved", "Buyback Order", self.name,
                  new_value={"approved_by": self.approved_by, "approved_price": self.approved_price})

    def reject(self, remarks=None):
        """Manager rejects the order."""
        if self.status != "Awaiting Approval":
            frappe.throw(
                _("Can only reject orders in 'Awaiting Approval' status."),
                exc=BuybackStatusError,
            )
        self.status = "Rejected"
        self.approved_by = frappe.session.user
        self.approval_date = now_datetime()
        if remarks:
            self.approval_remarks = remarks
        self.save()
        log_audit("Order Rejected", "Buyback Order", self.name,
                  new_value={"rejected_by": frappe.session.user, "reason": remarks})

    def send_otp(self):
        """Send OTP for customer verification."""
        from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

        if self.status not in ("Approved", "Awaiting OTP"):
            frappe.throw(
                _("OTP can only be sent after approval."),
                exc=BuybackStatusError,
            )

        otp_code = CHOTPLog.generate_otp(
            self.mobile_no,
            "Buyback Confirmation",
            reference_doctype="Buyback Order",
            reference_name=self.name,
        )
        self.status = "Awaiting OTP"
        self.save()
        log_audit("OTP Sent", "Buyback Order", self.name)
        return otp_code

    def verify_otp(self, otp_code):
        """Verify customer OTP."""
        from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

        if self.status != "Awaiting OTP":
            frappe.throw(
                _("OTP verification only applicable in 'Awaiting OTP' status."),
                exc=BuybackStatusError,
            )

        result = CHOTPLog.verify_otp(
            self.mobile_no,
            "Buyback Confirmation",
            otp_code,
            reference_doctype="Buyback Order",
            reference_name=self.name,
        )

        if result["valid"]:
            self.otp_verified = 1
            self.otp_verified_at = now_datetime()
            self.status = "OTP Verified"
            self.save()
            log_audit("OTP Verified", "Buyback Order", self.name)

        return result

    def mark_ready_to_pay(self):
        """Move to payment stage."""
        if self.status != "OTP Verified":
            frappe.throw(
                _("Must verify OTP before proceeding to payment."),
                exc=BuybackStatusError,
            )
        self.status = "Ready to Pay"
        self.save()

    def mark_paid(self):
        """Mark as fully paid."""
        if self.payment_status != "Paid":
            frappe.throw(
                _("Total paid must equal final price."),
                exc=BuybackStatusError,
            )
        self.status = "Paid"
        self.save()
        log_audit("Payment Made", "Buyback Order", self.name,
                  new_value={"total_paid": self.total_paid})

    def close(self):
        """Close the order after payment."""
        if self.status != "Paid":
            frappe.throw(
                _("Can only close paid orders."),
                exc=BuybackStatusError,
            )
        self.status = "Closed"
        self.save()
        log_audit("Order Closed", "Buyback Order", self.name)

    def _create_journal_entry(self):
        """
        Create a Journal Entry for the buyback expense.
        Dr: Buyback Expense Account
        Cr: Buyback Payable / Cash / Bank
        """
        settings = frappe.get_single("Buyback Settings")
        expense_account = settings.buyback_expense_account
        if not expense_account:
            frappe.logger("buyback").warning(
                f"No buyback_expense_account configured — skipping JE for {self.name}"
            )
            return

        company = self.company or settings.default_company
        if not company:
            return

        # Use default payable account from company
        payable_account = frappe.db.get_value(
            "Company", company, "default_payable_account"
        )
        if not payable_account:
            frappe.logger("buyback").warning(
                f"No default_payable_account for company {company} — skipping JE"
            )
            return

        je = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Journal Entry",
            "company": company,
            "posting_date": frappe.utils.nowdate(),
            "user_remark": f"Buyback Order {self.name} — {self.item_name or self.item}",
            "accounts": [
                {
                    "account": expense_account,
                    "debit_in_account_currency": flt(self.final_price),
                    "cost_center": frappe.db.get_value(
                        "Company", company, "cost_center"
                    ),
                },
                {
                    "account": payable_account,
                    "credit_in_account_currency": flt(self.final_price),
                    "party_type": "Customer",
                    "party": self.customer,
                },
            ],
        })
        je.insert(ignore_permissions=True)
        je.submit()
        self.journal_entry = je.name

    def _create_stock_entry(self):
        """
        Create a Stock Entry (Material Receipt) for the received buyback device.
        The device enters the buyback warehouse as used inventory.
        """
        settings = frappe.get_single("Buyback Settings")

        # Determine target warehouse: store warehouse > settings default > skip
        target_warehouse = None
        if self.store:
            target_warehouse = frappe.db.get_value("CH Store", self.store, "warehouse")
        if not target_warehouse:
            target_warehouse = frappe.db.get_value(
                "Company", self.company or settings.default_company, "default_warehouse"
            )
        if not target_warehouse:
            frappe.logger("buyback").warning(
                f"No warehouse resolved for {self.name} — skipping Stock Entry"
            )
            return

        # Determine valuation rate (use final_price as the cost of acquisition)
        valuation_rate = flt(self.final_price)

        se = frappe.get_doc({
            "doctype": "Stock Entry",
            "stock_entry_type": "Material Receipt",
            "company": self.company or settings.default_company,
            "posting_date": frappe.utils.nowdate(),
            "remarks": f"Buyback device received — {self.name}",
            "items": [
                {
                    "item_code": self.item,
                    "t_warehouse": target_warehouse,
                    "qty": 1,
                    "basic_rate": valuation_rate,
                    "serial_no": self.imei_serial or "",
                },
            ],
        })
        se.insert(ignore_permissions=True)
        se.submit()
        self.stock_entry = se.name
