"""
Bank of Baroda Integration Profile Setup
=========================================

Creates Bank Integration Profile configurations for BoB UAT testing.

Profiles to create:
1. BoB DigiNext UAT - Encrypted (with encryption/checksum keys)
2. BoB DigiNext UAT - Unencrypted (for testing without encryption)
"""

import frappe
from frappe.utils import today


def setup_bob_uat_profiles():
    """Create Bank Integration Profile configurations for BoB UAT"""
    
    print("\n" + "="*80)
    print("Setting up Bank of Baroda UAT Integration Profiles")
    print("="*80)
    
    # Profile 1: Encrypted with credentials
    profile1_data = {
        "doctype": "Bank Integration Profile",
        "profile_name": "BoB Olive UAT - Encrypted",
        "provider": "BoB DigiNext",
        "is_active": 1,
        "bank": "Bank of Baroda",  # Link to Bank master
        "company": "Your Company",  # Change this to your actual company
        "client_code": "059361561",
        "source_ip": "",  # Add if IP whitelisting required
        "encryption_key": "BZGS536SHET4634234E4445434243444",  # Will be stored as password
        "checksum_key": "TEST_CHECKSUM_KEY_FOR_UAT",  # Add actual key from bank
        "base_url": "https://apiuat.bankofbaroda.co.in:4443",
        "payment_initiation_path": "/olive/publisher/paymentTxn",
        "inquiry_path": "/olive/publisher/paymentsTxnInq",
        "callback_path": "/api/v1/bank/callback/bob",
        "supported_payment_modes": ["NEFT", "RTGS", "IMPS", "IFT"],
        "default_payment_mode": "NEFT",
        "rtgs_minimum_amount": 200000,  # 2L minimum for RTGS
        "max_transaction_amount": 10000000,  # 1 Cr max
        "require_beneficiary_registration": 0,
        "is_submember": 0,
    }
    
    profile2_data = {
        "doctype": "Bank Integration Profile",
        "profile_name": "BoB Olive UAT - Unencrypted",
        "provider": "BoB DigiNext",
        "is_active": 1,
        "bank": "Bank of Baroda",
        "company": "Your Company",  # Change this to your actual company
        "client_code": "RACHANA",
        "source_ip": "",
        "base_url": "https://apiuat.bankofbaroda.co.in:4443",
        "payment_initiation_path": "/olive/publisher/paymentTxn",
        "inquiry_path": "/olive/publisher/paymentsTxnInq",
        "callback_path": "/api/v1/bank/callback/bob",
        "supported_payment_modes": ["NEFT", "RTGS", "IMPS", "IFT"],
        "default_payment_mode": "NEFT",
        "rtgs_minimum_amount": 200000,
        "max_transaction_amount": 10000000,
        "require_beneficiary_registration": 0,
        "is_submember": 0,
    }
    
    profiles = [profile1_data, profile2_data]
    
    for profile_data in profiles:
        try:
            # Check if profile already exists
            existing = frappe.db.exists("Bank Integration Profile", profile_data["profile_name"])
            
            if existing:
                # Update existing profile
                doc = frappe.get_doc("Bank Integration Profile", profile_data["profile_name"])
                for key, value in profile_data.items():
                    if key != "doctype":
                        setattr(doc, key, value)
                doc.save()
                print(f"\n✅ Updated profile: {profile_data['profile_name']}")
            else:
                # Create new profile
                doc = frappe.get_doc(profile_data)
                doc.insert()
                print(f"\n✅ Created profile: {profile_data['profile_name']}")
            
            # Print profile details
            print(f"   Base URL: {doc.base_url}")
            print(f"   Client Code: {doc.client_code}")
            print(f"   Initiation Path: {doc.payment_initiation_path}")
            print(f"   Inquiry Path: {doc.inquiry_path}")
            
        except frappe.DuplicateEntryError:
            print(f"\n⚠️  Profile already exists: {profile_data['profile_name']}")
        except Exception as e:
            print(f"\n❌ Error creating profile {profile_data['profile_name']}: {str(e)}")
    
    print("\n" + "="*80)
    print("Bank Integration Profiles setup complete!")
    print("="*80)
    print("\nNext Steps:")
    print("1. Go to Bank Integration Profile in ERPNext UI")
    print("2. Update 'company' field to your actual company")
    print("3. Add 'debit_account' if needed")
    print("4. Test payment initiation via Payment API")
    print("\n")


if __name__ == "__main__":
    if frappe.db.count("DocType", {"name": "Bank Integration Profile"}) > 0:
        setup_bob_uat_profiles()
    else:
        print("❌ Bank Integration Profile DocType not found in ch_payments app")
        print("Make sure ch_payments app is installed")
