import frappe
import re
from frappe.model.document import Document


class BuybackRequest(Document):

    # -------------------------
    # BEFORE INSERT
    # -------------------------
    def before_insert(self):

        last = frappe.db.sql("""
            SELECT MAX(buybackid)
            FROM `tabBuyback Request`
            FOR UPDATE
        """)[0][0] or 0

        self.buybackid = last + 1
        self.created_by_user = frappe.session.user


    # -------------------------
    # MAIN VALIDATION
    # -------------------------
    def validate(self):

        if not self.deal_status:
            frappe.throw("Select Deal or No Deal")

        self.validate_customer()
        self.validate_product()

        if self.deal_status == "No Deal":
            self.validate_no_deal()
            return

        self.validate_deal()
        self.validate_payment()


    # -------------------------
    # CUSTOMER VALIDATION
    # -------------------------
    def validate_customer(self):

        required = {
            "Customer Name": self.customer_name,
            "Mobile No": self.mobile_no,
            "Address": self.address,
            "PIN Code": self.pincode,
            "Email": self.email
        }

        for label, value in required.items():
            if not value:
                frappe.throw(f"{label} is required")

        # 10 digit mobile validation
        mobile = (self.mobile_no or "").strip()
        if not re.fullmatch(r"\d{10}", mobile):
            frappe.throw("Mobile number must be exactly 10 digits")

        # 6 digit PIN validation
        pin = (self.pincode or "").strip()
        if not re.fullmatch(r"\d{6}", pin):
            frappe.throw("PIN code must be exactly 6 digits")

        # Gmail validation
        email = (self.email or "").strip().lower()
        if not re.fullmatch(r"[a-zA-Z0-9._%+-]+@gmail\.com", email):
            frappe.throw("Enter a valid Gmail address (example@gmail.com)")


    # -------------------------
    # PRODUCT VALIDATION
    # -------------------------
    def validate_product(self):

        required = {
            "Item Name": self.item_name,
            "Usage Months": self.usage_key,
            "Grade": self.grade
        }

        for label, value in required.items():
            if not value:
                frappe.throw(f"{label} is required")

        if self.buyback_price is None or self.buyback_price <= 0:
            frappe.throw("Invalid Buyback Price")


    # -------------------------
    # NO DEAL VALIDATION
    # -------------------------
    def validate_no_deal(self):

        if not self.no_deal_reason:
            frappe.throw("No Deal reason required")


    # -------------------------
    # DEAL VALIDATION
    # -------------------------
    def validate_deal(self):

        if not self.customer_image:
            frappe.throw("Customer Image required")

        if not self.aadhaar_pdf:
            frappe.throw("Aadhaar PDF required")

        if not self.upload_phone_images or len(self.upload_phone_images) == 0:
            frappe.throw("Upload Phone Images required")


    # -------------------------
    # PAYMENT VALIDATION
    # -------------------------
    def validate_payment(self):

        if not self.payment_mode_name:
            frappe.throw("Payment Mode required")

        mode = (self.payment_mode_name or "").lower().strip()

        # CASH
        if mode == "cash":

            if not self.cash_notes:
                frappe.throw("Cash notes required for Cash payment")

        # BANK TRANSFER
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
        elif mode == "upi":

            if not self.upi_id:
                frappe.throw("UPI ID required")

            if not self.transaction_proof:
                frappe.throw("Transaction proof required for UPI")

        else:
            frappe.throw(f"Invalid payment mode: {self.payment_mode_name}")


