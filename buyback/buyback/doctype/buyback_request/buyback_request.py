import frappe
from frappe.model.document import Document


class BuybackRequest(Document):

    def before_insert(self):

        # auto buyback id
        last = frappe.db.sql("""
            SELECT MAX(buybackid) FROM `tabBuyback Request`
        """)[0][0] or 0

        self.buybackid = last + 1

        # store login user
        self.created_by_user = frappe.session.user

    def validate(self):
        self.validate_payment()

    def validate_payment(self):

        if not self.payment_mode_name:
            frappe.throw("Payment Mode required")

        mode = self.payment_mode_name.lower()

        # CASH
        if "Cash" in mode:
            if not self.cash_notes:
                frappe.throw("Cash notes required")

        # BANK
        elif "bank" in mode:
            required = {
                "Account Holder Name": self.account_holder_name,
                "Branch": self.branch,
                "Bank Name": self.bank_name,
                "IFSC Code": self.ifsc_code,
                "Transaction Proof": self.transaction_proof
            }

            for label, value in required.items():
                if not value:
                    frappe.throw(f"{label} is required for Bank Transfer")

        # UPI
        elif "upi" in mode:
            if not self.upi_id:
                frappe.throw("UPI ID required")

            if not self.transaction_proof:
                frappe.throw("Transaction proof required")
