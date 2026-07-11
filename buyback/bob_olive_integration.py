"""
Bank of Baroda Olive Platform Integration
==========================================

Specialization of the payment API for BoB's new Olive platform.
Handles customer payouts via Bank Transfer (NEFT/RTGS/IMPS).

Usage:
    from buyback.bob_olive_integration import BoBOlivePayout
    
    payout = BoBOlivePayout(buyback_order_doc)
    response = payout.initiate_payout()
    status = payout.check_status()
    
"""

import frappe
from frappe import _
from frappe.utils import formatdate, now_datetime
from datetime import datetime, timedelta
import json


class BoBOlivePayout:
    """
    Bank of Baroda Olive Platform payment orchestration.
    
    Workflow:
    1. Validate customer payout details
    2. Create Bank Payment Request
    3. Get Bank Integration Profile
    4. Call provider.initiate_payment()
    5. Track status via polling
    """
    
    def __init__(self, buyback_order):
        """Initialize with Buyback Order document"""
        self.buyback_order = buyback_order
        self.bank_profile = None
        self.provider = None
        self.bank_payment_request = None
        self.errors = []
        
        # Validate prerequisites
        self._validate_order()
        self._get_bank_profile()
    
    def _validate_order(self):
        """Validate Buyback Order prerequisites"""
        errors = []
        
        # Check payout mode
        if self.buyback_order.customer_payout_mode != "Bank Transfer":
            errors.append(f"Payout mode must be 'Bank Transfer', got '{self.buyback_order.customer_payout_mode}'")
        
        # Check customer bank details
        if not self.buyback_order.customer_bank_account_number:
            errors.append("Customer bank account number not provided")
        if not self.buyback_order.customer_bank_ifsc:
            errors.append("Customer bank IFSC not provided")
        if not self.buyback_order.customer_bank_account_holder:
            errors.append("Customer bank account holder name not provided")
        
        # Check payout amount
        if not self.buyback_order.total_payable_amount or self.buyback_order.total_payable_amount <= 0:
            errors.append("Invalid payout amount")
        
        if errors:
            frappe.throw(_("Cannot initiate payout:\n" + "\n".join(errors)))
        
        self.errors = errors
    
    def _get_bank_profile(self):
        """Get Bank Integration Profile for BoB Olive"""
        # Try to get active BoB Olive profile
        profiles = frappe.db.get_list(
            "Bank Integration Profile",
            filters={
                "provider": "BoB DigiNext",
                "is_active": 1,
            },
            fields=["name", "client_code", "base_url", "company"],
            limit=1
        )
        
        if not profiles:
            frappe.throw(_("No active Bank Integration Profile found for BoB DigiNext. Please configure one in Setup → Bank Integration Profile"))
        
        # Load full profile
        self.bank_profile = frappe.get_doc("Bank Integration Profile", profiles[0].name)
        
        if self.bank_profile.company != self.buyback_order.company:
            frappe.msgprint(
                _("Warning: Bank profile company ({0}) differs from order company ({1})").format(
                    self.bank_profile.company, self.buyback_order.company
                ),
                indicator="yellow"
            )
    
    def initiate_payout(self):
        """
        Initiate payment through BoB Olive API
        
        Returns:
            {
                "status": "success" | "error",
                "cms_ref": "API...",  # CMS Reference from bank
                "bank_ref": "...",     # Bank reference
                "message": "..."
            }
        """
        try:
            # Create Bank Payment Request
            bpr = self._create_bank_payment_request()
            
            # Get provider instance
            from ch_payments.bank_payments.providers import get_provider
            provider = get_provider(self.bank_profile.provider)
            
            # Initiate payment via API
            api_response = provider.initiate_payment(bpr, self.bank_profile)
            
            # Update BPR with API response
            bpr.cms_ref = api_response.get("cmsRef")
            bpr.bank_ref = api_response.get("bankRef")
            bpr.status = "Sent To Bank" if api_response.get("status") == "S" else "Failed"
            bpr.response_data = json.dumps(api_response)
            bpr.save()
            
            # Update Buyback Order
            self.buyback_order.custom_bank_payment_request = bpr.name
            self.buyback_order.save()
            
            # Log event
            self._log_payout_event("Payout Sent To Bank", api_response)
            
            return {
                "status": "success" if api_response.get("status") == "S" else "error",
                "cms_ref": api_response.get("cmsRef"),
                "bank_ref": api_response.get("bankRef"),
                "utr": api_response.get("UTR"),
                "message": api_response.get("statusDesc", "Payment initiated")
            }
            
        except Exception as e:
            self._log_payout_event("Payout Failed", {"error": str(e)})
            frappe.throw(_("Payment initiation failed: {0}").format(str(e)))
    
    def _create_bank_payment_request(self):
        """Create Bank Payment Request document"""
        bpr_doc = frappe.new_doc("Bank Payment Request")
        bpr_doc.customer_txn_ref = f"BO-{self.buyback_order.name}-{datetime.now().strftime('%s')}"
        bpr_doc.payment_method = "Bank Transfer"
        bpr_doc.payment_mode = self._get_payment_mode()  # NEFT/RTGS/IMPS
        bpr_doc.transaction_amount = self.buyback_order.total_payable_amount
        bpr_doc.payment_date = formatdate(datetime.now() + timedelta(days=1), "yyyy-mm-dd")
        
        # Beneficiary details
        bpr_doc.beneficiary_name = self.buyback_order.customer_bank_account_holder
        bpr_doc.beneficiary_account_no = self.buyback_order.customer_bank_account_number
        bpr_doc.beneficiary_ifsc = self.buyback_order.customer_bank_ifsc
        bpr_doc.beneficiary_email = self.buyback_order.customer_email
        bpr_doc.beneficiary_mobile = self.buyback_order.customer_mobile
        
        # Debit account (from Bank Integration Profile)
        if self.bank_profile.debit_account:
            bpr_doc.debit_account_no = self.bank_profile.debit_account
        
        # Link to Buyback Order
        bpr_doc.reference_doctype = "Buyback Order"
        bpr_doc.reference_name = self.buyback_order.name
        
        bpr_doc.save()
        bpr_doc.submit()
        
        return bpr_doc
    
    def _get_payment_mode(self):
        """
        Determine payment mode (NEFT/RTGS/IMPS) based on amount.
        
        Rules:
        - If amount >= 2L (200000): Use RTGS (next-day settlement)
        - Otherwise: Use NEFT (usually same-day/next-day)
        - For amounts < 1L within state: Use IMPS if available
        """
        amount = self.buyback_order.total_payable_amount
        rtgs_min = self.bank_profile.rtgs_minimum_amount or 200000
        
        if amount >= rtgs_min:
            return "RTGS"
        else:
            return "NEFT"  # Default to NEFT
    
    def check_status(self):
        """
        Check payment status from Bank Payment Request
        
        Returns:
            {
                "status": "Pending" | "Sent To Bank" | "Processed" | "Failed",
                "cms_ref": "...",
                "bank_ref": "...",
                "utr": "...",
                "message": "..."
            }
        """
        if not self.buyback_order.custom_bank_payment_request:
            return {
                "status": "not_initiated",
                "message": "Payment not yet initiated"
            }
        
        bpr = frappe.get_doc("Bank Payment Request", self.buyback_order.custom_bank_payment_request)
        
        return {
            "status": bpr.status.lower(),
            "cms_ref": bpr.cms_ref,
            "bank_ref": bpr.bank_ref,
            "utr": bpr.utr,
            "payment_date": bpr.payment_date,
            "message": bpr.status
        }
    
    def refresh_status(self):
        """
        Refresh payment status by querying the bank API
        
        Returns:
            Latest status from bank
        """
        if not self.buyback_order.custom_bank_payment_request:
            frappe.throw(_("No payment initiated yet"))
        
        bpr = frappe.get_doc("Bank Payment Request", self.buyback_order.custom_bank_payment_request)
        
        try:
            # Get provider instance
            from ch_payments.bank_payments.providers import get_provider
            provider = get_provider(self.bank_profile.provider)
            
            # Query bank API for status
            api_response = provider.inquire_payment(bpr, self.bank_profile)
            
            # Update BPR
            bpr.response_data = json.dumps(api_response)
            payment_status = api_response.get("paymentStatus", "Unknown")
            
            # Map bank status to our status
            if payment_status == "Settled":
                bpr.status = "Processed"
            elif payment_status == "Pending":
                bpr.status = "Sent To Bank"
            elif payment_status in ["Rejected", "Failed", "Cancelled"]:
                bpr.status = "Failed"
            
            bpr.save()
            
            # Log event
            self._log_payout_event("Status Checked", api_response)
            
            return {
                "status": "success",
                "bank_status": payment_status,
                "utr": api_response.get("UTR"),
                "message": "Status updated from bank"
            }
            
        except Exception as e:
            frappe.msgprint(_("Could not refresh status: {0}").format(str(e)))
            return {"status": "error", "message": str(e)}
    
    def _log_payout_event(self, event_type, event_data):
        """
        Log payout event for audit trail
        
        Creates a record in a custom Payment Audit Log for compliance
        """
        try:
            audit_log = frappe.new_doc("Payment Audit Log")
            audit_log.event_type = event_type
            audit_log.buyback_order = self.buyback_order.name
            audit_log.customer = self.buyback_order.customer
            audit_log.event_data = json.dumps(event_data)
            audit_log.timestamp = now_datetime()
            audit_log.user = frappe.session.user
            audit_log.save()
        except:
            # Don't fail the payout if audit log creation fails
            pass
    
    def get_payout_details(self):
        """Get formatted payout details for customer portal"""
        return {
            "order_id": self.buyback_order.name,
            "customer": self.buyback_order.customer,
            "amount": self.buyback_order.total_payable_amount,
            "payout_mode": self.buyback_order.customer_payout_mode,
            "account": self.buyback_order.customer_bank_account_number,
            "account_holder": self.buyback_order.customer_bank_account_holder,
            "ifsc": self.buyback_order.customer_bank_ifsc,
            "status": self.check_status().get("status"),
            "utr": self.check_status().get("utr"),
        }


def initiate_buyback_payout(order_name):
    """
    API entry point: Initiate payout for a Buyback Order
    
    Usage:
        frappe.call({
            "method": "buyback.bob_olive_integration.initiate_buyback_payout",
            "args": {"order_name": "BO-00001"},
            "callback": function(r) { ... }
        })
    """
    order = frappe.get_doc("Buyback Order", order_name)
    payout = BoBOlivePayout(order)
    return payout.initiate_payout()


def refresh_buyback_payout_status(order_name):
    """
    API entry point: Refresh payout status for a Buyback Order
    """
    order = frappe.get_doc("Buyback Order", order_name)
    payout = BoBOlivePayout(order)
    return payout.refresh_status()


def get_buyback_payout_details(order_name):
    """
    API entry point: Get payout details for customer portal
    """
    order = frappe.get_doc("Buyback Order", order_name)
    payout = BoBOlivePayout(order)
    return payout.get_payout_details()
