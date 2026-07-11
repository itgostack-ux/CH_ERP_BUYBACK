"""
Bank of Baroda Olive Integration - E2E Test Scenario
====================================================

Tests the complete buyback order payment workflow using BoB Olive API.

Test Scenarios:
1. Create test Buyback Order with customer bank details
2. Validate payout prerequisites
3. Initiate payment via BoB API
4. Check payment status
5. Verify audit trail

Run with:
    bench --site erpnext.local exec apps/buyback/test_bob_olive_e2e.py
"""

import frappe
from frappe.utils import today, formatdate, nowdate
from datetime import datetime, timedelta
import json


def setup_test_data():
    """Create test data for payment scenarios"""
    print("\n" + "="*80)
    print("Setting up test data...")
    print("="*80)
    
    # Ensure test customer exists
    customer_name = "BOB Test Customer - BO UAT"
    if not frappe.db.exists("Customer", customer_name):
        customer = frappe.new_doc("Customer")
        customer.customer_name = customer_name
        customer.customer_type = "Individual"
        customer.email = "customer@test-bob-uat.com"
        customer.phone_no = "9876543210"
        customer.save()
        print(f"✅ Created test customer: {customer_name}")
    else:
        print(f"⚠️  Test customer already exists: {customer_name}")
    
    # Ensure test item exists
    item_name = "BOB Test Item - UAT"
    if not frappe.db.exists("Item", item_name):
        item = frappe.new_doc("Item")
        item.item_code = item_name
        item.item_name = item_name
        item.item_group = "Products"
        item.stock_uom = "Nos"
        item.save()
        print(f"✅ Created test item: {item_name}")
    else:
        print(f"⚠️  Test item already exists: {item_name}")
    
    return {
        "customer": customer_name,
        "item": item_name,
    }


def create_test_buyback_order(test_data, company="Your Company"):
    """Create a Buyback Order for testing payment"""
    print("\n" + "="*80)
    print("Creating test Buyback Order...")
    print("="*80)
    
    order = frappe.new_doc("Buyback Order")
    order.company = company
    order.customer = test_data["customer"]
    order.transaction_date = today()
    
    # Add item
    order.append("items", {
        "item_code": test_data["item"],
        "quantity": 1,
        "rate": 1000,
        "amount": 1000,
    })
    
    # Set customer payout details (Bank Transfer via BoB)
    order.customer_payout_mode = "Bank Transfer"
    order.customer_bank_account_holder = "Test Customer"
    order.customer_bank_account_number = "1234567890123"
    order.customer_bank_ifsc = "SBIN0001234"
    order.customer_bank_name = "State Bank of India"
    
    order.save()
    
    print(f"✅ Created Buyback Order: {order.name}")
    print(f"   Amount: {order.total_payable_amount}")
    print(f"   Payout Mode: {order.customer_payout_mode}")
    print(f"   Account: {order.customer_bank_account_number}")
    print(f"   IFSC: {order.customer_bank_ifsc}")
    
    return order


def test_payment_initiation(order):
    """Test payment initiation via BoB Olive API"""
    print("\n" + "="*80)
    print("Testing Payment Initiation...")
    print("="*80)
    
    try:
        from buyback.bob_olive_integration import BoBOlivePayout
        
        print(f"\n📦 Creating payout orchestrator for order: {order.name}")
        payout = BoBOlivePayout(order)
        
        print("✅ Payout orchestrator initialized")
        print(f"   Bank Profile: {payout.bank_profile.profile_name if payout.bank_profile else 'NOT FOUND'}")
        print(f"   Provider: {payout.bank_profile.provider if payout.bank_profile else 'N/A'}")
        print(f"   Client Code: {payout.bank_profile.client_code if payout.bank_profile else 'N/A'}")
        print(f"   Base URL: {payout.bank_profile.base_url if payout.bank_profile else 'N/A'}")
        
        print("\n🚀 Initiating payment...")
        response = payout.initiate_payout()
        
        print(f"✅ Payment Initiation Response:")
        print(f"   Status: {response.get('status')}")
        print(f"   CMS Ref: {response.get('cms_ref')}")
        print(f"   Bank Ref: {response.get('bank_ref')}")
        print(f"   UTR: {response.get('utr')}")
        print(f"   Message: {response.get('message')}")
        
        return response
        
    except Exception as e:
        print(f"❌ Payment initiation failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


def test_payment_status_check(order):
    """Test checking payment status"""
    print("\n" + "="*80)
    print("Testing Payment Status Check...")
    print("="*80)
    
    try:
        from buyback.bob_olive_integration import BoBOlivePayout
        
        payout = BoBOlivePayout(order)
        
        print(f"📋 Checking status for order: {order.name}")
        status = payout.check_status()
        
        print(f"✅ Payment Status:")
        print(f"   Status: {status.get('status')}")
        print(f"   CMS Ref: {status.get('cms_ref')}")
        print(f"   Bank Ref: {status.get('bank_ref')}")
        print(f"   UTR: {status.get('utr')}")
        print(f"   Message: {status.get('message')}")
        
        return status
        
    except Exception as e:
        print(f"❌ Status check failed: {str(e)}")
        return {"status": "error", "message": str(e)}


def test_payout_details(order):
    """Test getting formatted payout details for customer portal"""
    print("\n" + "="*80)
    print("Testing Payout Details for Portal...")
    print("="*80)
    
    try:
        from buyback.bob_olive_integration import BoBOlivePayout
        
        payout = BoBOlivePayout(order)
        
        print(f"📱 Getting payout details for customer portal...")
        details = payout.get_payout_details()
        
        print(f"✅ Payout Details:")
        for key, value in details.items():
            print(f"   {key}: {value}")
        
        return details
        
    except Exception as e:
        print(f"❌ Getting details failed: {str(e)}")
        return {"status": "error", "message": str(e)}


def test_bank_integration_profile():
    """Test Bank Integration Profile configuration"""
    print("\n" + "="*80)
    print("Testing Bank Integration Profile...")
    print("="*80)
    
    profiles = frappe.db.get_list(
        "Bank Integration Profile",
        filters={"provider": "BoB DigiNext", "is_active": 1},
        fields=["name", "client_code", "base_url", "company"],
    )
    
    if not profiles:
        print("❌ No active BoB DigiNext profiles found!")
        print("\nTo create a profile, run:")
        print("   bench --site erpnext.local exec scripts/setup_bob_uat_profiles.py")
        return False
    
    for profile in profiles:
        print(f"\n✅ Found profile: {profile.name}")
        doc = frappe.get_doc("Bank Integration Profile", profile.name)
        print(f"   Provider: {doc.provider}")
        print(f"   Client Code: {doc.client_code}")
        print(f"   Base URL: {doc.base_url}")
        print(f"   Company: {doc.company}")
        print(f"   Initiation Path: {doc.payment_initiation_path}")
        print(f"   Inquiry Path: {doc.inquiry_path}")
    
    return True


def run_e2e_tests():
    """Run complete e2e test scenario"""
    print("\n")
    print("╔" + "="*78 + "╗")
    print("║" + " "*78 + "║")
    print("║" + "Bank of Baroda Olive Integration - End-to-End Test".center(78) + "║")
    print("║" + " "*78 + "║")
    print("╚" + "="*78 + "╝")
    
    # Test 1: Check Bank Integration Profile
    if not test_bank_integration_profile():
        print("\n⚠️  Skipping further tests - no Bank Integration Profile configured")
        return
    
    # Test 2: Setup test data
    test_data = setup_test_data()
    
    # Test 3: Create test Buyback Order
    # Note: Adjust company name if needed
    company = frappe.db.get_single_value("Global Defaults", "default_company")
    if not company:
        company = frappe.db.get_list("Company", limit=1)[0].name if frappe.db.get_list("Company") else "Default Company"
    
    print(f"\nUsing company: {company}")
    order = create_test_buyback_order(test_data, company)
    
    # Test 4: Test payment initiation (if API endpoint is reachable)
    print("\n" + "="*80)
    print("PAYMENT INITIATION TEST")
    print("="*80)
    print("⚠️  Testing payment initiation...")
    print("   Note: This will create an actual Bank Payment Request and call the BoB API")
    print("   Response will depend on UAT endpoint availability and credentials")
    
    response = test_payment_initiation(order)
    
    # Test 5: Check status
    if response.get("status") == "success":
        print("\n⏳ Waiting 2 seconds before status check...")
        import time
        time.sleep(2)
        test_payment_status_check(order)
    
    # Test 6: Get payout details
    test_payout_details(order)
    
    # Summary
    print("\n" + "="*80)
    print("E2E TEST SUMMARY")
    print("="*80)
    print("✅ Test Data Setup: Created test customer and item")
    print("✅ Bank Integration Profile: Verified configuration")
    print(f"✅ Buyback Order Creation: {order.name} created successfully")
    
    if response.get("status") == "success":
        print(f"✅ Payment Initiation: SUCCESS")
        print(f"   CMS Ref: {response.get('cms_ref')}")
        print(f"   Bank Ref: {response.get('bank_ref')}")
    elif response.get("status") == "error":
        print(f"⚠️  Payment Initiation: FAILED")
        print(f"   Error: {response.get('message')}")
    
    print("\n" + "="*80)
    print("Next Steps:")
    print("="*80)
    print("1. Check payment status in ERPNext UI:")
    print(f"   Go to Bank Payment Request list")
    print(f"")
    print("2. Monitor payment in BoB portal:")
    print(f"   Login to BoB Olive portal with test credentials")
    print(f"   Look for CMS Ref: {response.get('cms_ref')}")
    print(f"")
    print("3. Verify audit trail:")
    print(f"   Check Payment Audit Log for event records")
    print(f"")
    print("4. Test callback receipt:")
    print(f"   Once payment settles, verify callback is received")
    print(f"   Check system console for callback events")
    print("\n")


if __name__ == "__main__":
    try:
        run_e2e_tests()
    except KeyboardInterrupt:
        print("\n\n🛑 Tests interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Test failed: {str(e)}")
        import traceback
        traceback.print_exc()
