import frappe
import re
from frappe.model.document import Document
from frappe.utils import get_url, validate_email_address


def send_approval_email(doc):
    """Send approval email to customer"""

    if not doc.email:
        return

    base_url = get_url()
    approval_link = f"{base_url}/approval?id={doc.buybackid}"

    message = f"""
    <h3>Buyback Approval Required</h3>

    <p>Please review and approve the request:</p>

    <p>
        <a href="{approval_link}"
           style="background:#28a745;
                  color:white;
                  padding:12px 20px;
                  text-decoration:none;
                  border-radius:6px;">
            Open Approval Page
        </a>
    </p>

    <p>Request ID: {doc.buybackid}</p>
    """

    frappe.sendmail(
        recipients=[doc.email],
        subject="Buyback Approval Required",
        message=message,
        now=False,
    )



class BuybackRequest(Document):

    # -----------------------------------------------------
    # BEFORE INSERT
    # -----------------------------------------------------
    def before_insert(self):
        """Generate incremental buybackid with advisory lock."""

        frappe.db.sql("SELECT GET_LOCK('buyback_request_id', 10)")
        try:
            last = frappe.db.sql(
                """SELECT MAX(buybackid) FROM `tabBuyback Request`"""
            )[0][0] or 0
            self.buybackid = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('buyback_request_id')")

    # -----------------------------------------------------
    # AFTER INSERT
    # -----------------------------------------------------
    def after_insert(self):
        """Send email only for valid Deal"""

        deal_status = (self.deal_status or "").strip().lower()

        if deal_status != "deal":
            return

        if not self.email:
            return

        if not self.buyback_price or float(self.buyback_price) <= 0:
            return

        send_approval_email(self)

    # -----------------------------------------------------
    # MAIN VALIDATION
    # -----------------------------------------------------
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

    # -----------------------------------------------------
    # CUSTOMER VALIDATION
    # -----------------------------------------------------
    def validate_customer(self):

        required = {
            "Customer Name": self.customer_name,
            "Mobile No": self.mobile_no,
            "Address": self.address,
            "PIN Code": self.pincode,
            "Email": self.email,
        }

        for label, value in required.items():
            if not value:
                frappe.throw(f"{label} is required")

        mobile = (self.mobile_no or "").strip()
        if not re.fullmatch(r"\d{10}", mobile):
            frappe.throw("Mobile number must be exactly 10 digits")
        self.mobile_no = mobile

        pin = (self.pincode or "").strip()
        if not re.fullmatch(r"\d{6}", pin):
            frappe.throw("PIN code must be exactly 6 digits")
        self.pincode = pin

        self.email = (self.email or "").strip().lower()
        validate_email_address(self.email, throw=True)

    # -----------------------------------------------------
    # PRODUCT VALIDATION
    # -----------------------------------------------------
    def validate_product(self):
        """Validate selected item and pricing"""

        required = {
            "Item": self.item_id,
            "Usage Months": self.usage_key,
            "Grade": self.grade,
        }

        for label, value in required.items():
            if not value:
                frappe.throw(f"{label} is required")

        if self.buyback_price is None or self.buyback_price <= 0:
            frappe.throw("Invalid Buyback Price")

    # -----------------------------------------------------
    # NO DEAL VALIDATION
    # -----------------------------------------------------
    def validate_no_deal(self):
        if not self.no_deal_reason:
            frappe.throw("No Deal reason required")

    # -----------------------------------------------------
    # DEAL VALIDATION
    # -----------------------------------------------------
    def validate_deal(self):

        if not self.customer_image:
            frappe.throw("Customer Image required")

        if not self.aadhaar_pdf:
            frappe.throw("Aadhaar PDF required")

        if not self.upload_phone_images:
            frappe.throw("Upload Phone Images required")

    # -----------------------------------------------------
    # PAYMENT VALIDATION
    # -----------------------------------------------------
    def validate_payment(self):

        if not self.payment_mode_name:
            frappe.throw("Payment Mode required")

        mode = (self.payment_mode_name or "").lower().strip()

        # ---------------- CASH ----------------
        if mode == "cash":
            if not self.cash_notes:
                frappe.throw("Cash notes required for Cash payment")

        # ---------------- BANK ----------------
        elif "bank transfer" in mode:
            required = {
                "Account Holder Name": self.account_holder_name,
                "Branch": self.branch,
                "Bank Name": self.bank_name,
                "IFSC Code": self.ifsc_code,
                "Transaction Proof": self.transaction_proof,
            }

            for label, value in required.items():
                if not value:
                    frappe.throw(f"{label} is required for Bank Transfer")

        # ---------------- UPI ----------------
        elif mode == "upi":
            if not self.upi_id:
                frappe.throw("UPI ID required")

            if not self.transaction_proof:
                frappe.throw("Transaction proof required for UPI")

        else:
            frappe.throw(f"Invalid payment mode: {self.payment_mode_name}")