"""
Bank of Baroda (BoB) UAT Integration Test & Setup
===============================================

Tests connection to BoB's new Olive platform UAT endpoints:
- https://apiuat.bankofbaroda.co.in:4443/olive/publisher/paymentTxn
- https://apiuat.bankofbaroda.co.in:4443/olive/publisher/paymentsTxnInq

Testing Scenarios:
1. With Encryption (Client Code: 059361561, Account: 04520200000401)
2. Without Encryption (Client Code: RACHANA, Account: 09570200000585)
"""

import json
import hashlib
import hmac
import requests
from datetime import datetime, timedelta

# ============================================================================
# UAT Configuration
# ============================================================================

BOB_UAT_CONFIG = {
    "encrypted": {
        "base_url": "https://apiuat.bankofbaroda.co.in:4443",
        "payment_txn_endpoint": "/olive/publisher/paymentTxn",
        "payment_inquiry_endpoint": "/olive/publisher/paymentsTxnInq",
        "client_code": "059361561",
        "account_no": "04520200000401",
        "encryption_key": "BZGS536SHET4634234E4445434243444",
        "encryption_iv": "cM5ApI1nitVect0r",
    },
    "unencrypted": {
        "base_url": "https://apiuat.bankofbaroda.co.in:4443",
        "payment_txn_endpoint": "/olive/publisher/paymentTxn",
        "payment_inquiry_endpoint": "/olive/publisher/paymentsTxnInq",
        "client_code": "RACHANA",
        "account_no": "09570200000585",
    }
}

# ============================================================================
# Test Data
# ============================================================================

TEST_BENEFICIARY = {
    "name": "Test Customer",
    "account_no": "1234567890123",
    "ifsc": "SBIN0001234",
    "email": "customer@example.com",
    "mobile": "9876543210",
}

# ============================================================================
# Helper Functions
# ============================================================================

def aes_encrypt(plaintext, key, iv):
    """
    AES-256-CBC encryption
    
    Args:
        plaintext: JSON data to encrypt
        key: 32-byte hex encryption key
        iv: 16-byte hex initialization vector
    
    Returns:
        Base64-encoded ciphertext
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    import base64
    
    # Convert hex key/iv to bytes
    key_bytes = bytes.fromhex(key) if isinstance(key, str) and len(key) == 64 else key.encode('utf-8').ljust(32)[:32]
    iv_bytes = bytes.fromhex(iv) if isinstance(iv, str) and len(iv) == 32 else iv.encode('utf-8').ljust(16)[:16]
    
    # Add PKCS7 padding
    block_size = 16
    padding_length = block_size - (len(plaintext) % block_size)
    padded_plaintext = plaintext + chr(padding_length) * padding_length
    
    # Encrypt
    cipher = Cipher(
        algorithms.AES(key_bytes),
        modes.CBC(iv_bytes),
        backend=default_backend()
    )
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded_plaintext.encode('utf-8')) + encryptor.finalize()
    
    # Return base64-encoded ciphertext
    return base64.b64encode(ciphertext).decode('utf-8')


def compute_checksum(data, checksum_key):
    """
    Compute SHA-256 HMAC checksum
    
    Args:
        data: Dictionary of data to checksum
        checksum_key: Secret key for HMAC
    
    Returns:
        Hex-encoded SHA-256 HMAC
    """
    # Sort keys for deterministic ordering
    sorted_vals = []
    for key in sorted(data.keys()):
        val = data[key]
        if val is not None and key not in ("checksum", "signature"):
            if isinstance(val, (list, dict)):
                sorted_vals.append(json.dumps(val, separators=(",", ":")))
            else:
                sorted_vals.append(str(val))
    
    message = "|".join(sorted_vals)
    return hmac.new(
        checksum_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ============================================================================
# Test Scenarios
# ============================================================================

class BoBUATTester:
    def __init__(self, config_name="encrypted"):
        self.config = BOB_UAT_CONFIG[config_name]
        self.config_name = config_name
        self.test_results = []
    
    def test_connectivity(self):
        """Test basic connectivity to UAT endpoints"""
        print(f"\n{'='*70}")
        print(f"Testing Bank of Baroda UAT ({self.config_name.upper()})")
        print(f"{'='*70}")
        
        endpoints = [
            ("Payment Initiation", self.config["payment_txn_endpoint"]),
            ("Payment Inquiry", self.config["payment_inquiry_endpoint"]),
        ]
        
        for name, endpoint in endpoints:
            url = self.config["base_url"] + endpoint
            try:
                # Send OPTIONS request to check connectivity
                response = requests.options(url, timeout=10, verify=False)
                status = "✅ REACHABLE" if response.status_code in [200, 404, 405] else "❌ ERROR"
                print(f"{name:30} {status:15} (HTTP {response.status_code})")
                self.test_results.append({
                    "test": name,
                    "status": "reachable" if response.status_code < 500 else "unreachable",
                    "http_code": response.status_code
                })
            except requests.exceptions.Timeout:
                print(f"{name:30} {'❌ TIMEOUT':15}")
                self.test_results.append({"test": name, "status": "timeout"})
            except requests.exceptions.ConnectionError as e:
                print(f"{name:30} {'❌ UNREACHABLE':15}")
                self.test_results.append({"test": name, "status": "unreachable"})
            except Exception as e:
                print(f"{name:30} {'❌ ERROR':15} {str(e)[:40]}")
                self.test_results.append({"test": name, "status": "error", "error": str(e)})
    
    def build_payment_initiation_payload(self):
        """Build payment initiation payload"""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d-%m-%Y")
        
        payload = {
            "custTxnRef": f"TEST-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "clientCode": self.config["client_code"],
            "tranAmount": "1000.00",
            "paymentType": "NEFT",
            "valueDate": tomorrow,
            "isSubmbr": "N",
            "beneAccNo": TEST_BENEFICIARY["account_no"],
            "IFSC": TEST_BENEFICIARY["ifsc"],
            "beneName": TEST_BENEFICIARY["name"],
            "beneMail": TEST_BENEFICIARY["email"],
            "beneMobile": TEST_BENEFICIARY["mobile"],
            "debitAcNo": self.config["account_no"],
            "beneAdd1": "Test Address",
            "invDtlReq": "N",
        }
        
        return payload
    
    def test_encryption(self):
        """Test encryption/checksum logic"""
        if self.config_name != "encrypted":
            print("\n⏭️  Skipping encryption test (unencrypted mode)")
            return
        
        print(f"\n{'='*70}")
        print("Testing Encryption & Checksum")
        print(f"{'='*70}")
        
        payload = self.build_payment_initiation_payload()
        print(f"\n📦 Original Payload:")
        print(json.dumps(payload, indent=2))
        
        # Compute checksum (using a test key)
        test_checksum_key = "TEST_CHECKSUM_KEY_FOR_UAT"
        checksum = compute_checksum(payload, test_checksum_key)
        print(f"\n🔐 Checksum (SHA-256 HMAC):")
        print(f"   {checksum}")
        
        # Add checksum to payload
        payload["checksum"] = checksum
        
        # Encrypt payload
        try:
            encrypted_data = aes_encrypt(
                json.dumps(payload, separators=(",", ":")),
                self.config["encryption_key"],
                self.config["encryption_iv"]
            )
            print(f"\n🔒 Encrypted Payload (AES-256-CBC):")
            print(f"   {encrypted_data[:80]}...")
            
            # Build request body
            request_body = {
                "paymentTxnReq": {
                    "clientCode": self.config["client_code"],
                    "data": encrypted_data,
                }
            }
            print(f"\n📤 Request Body:")
            print(json.dumps(request_body, indent=2)[:200] + "...")
            
            self.test_results.append({
                "test": "Encryption & Checksum",
                "status": "success",
                "encrypted_length": len(encrypted_data),
                "checksum": checksum
            })
        except Exception as e:
            print(f"\n❌ Encryption failed: {str(e)}")
            self.test_results.append({
                "test": "Encryption & Checksum",
                "status": "failed",
                "error": str(e)
            })
    
    def test_payment_initiation(self):
        """Test payment initiation API call"""
        print(f"\n{'='*70}")
        print("Testing Payment Initiation (paymentTxn)")
        print(f"{'='*70}")
        
        url = self.config["base_url"] + self.config["payment_txn_endpoint"]
        print(f"\n🌐 Endpoint: {url}")
        
        payload = self.build_payment_initiation_payload()
        
        # Build request body
        if self.config_name == "encrypted":
            try:
                encrypted_data = aes_encrypt(
                    json.dumps(payload, separators=(",", ":")),
                    self.config["encryption_key"],
                    self.config["encryption_iv"]
                )
                request_body = {
                    "paymentTxnReq": {
                        "clientCode": self.config["client_code"],
                        "data": encrypted_data,
                    }
                }
            except Exception as e:
                print(f"❌ Encryption failed: {str(e)}")
                return
        else:
            request_body = {
                "paymentTxnReq": {
                    "clientCode": self.config["client_code"],
                    "data": json.dumps(payload),
                }
            }
        
        print(f"\n📤 Request:")
        req_display = json.dumps(request_body, indent=2)
        print(req_display[:300] + ("..." if len(req_display) > 300 else ""))
        
        try:
            headers = {"Content-Type": "application/json"}
            response = requests.post(url, json=request_body, headers=headers, timeout=30, verify=False)
            
            print(f"\n📬 Response Status: HTTP {response.status_code}")
            
            try:
                response_data = response.json()
                print(f"📬 Response Body:")
                print(json.dumps(response_data, indent=2))
                self.test_results.append({
                    "test": "Payment Initiation",
                    "status": "completed",
                    "http_code": response.status_code,
                    "response": response_data
                })
            except:
                print(f"📬 Response (raw):")
                print(response.text[:500])
                self.test_results.append({
                    "test": "Payment Initiation",
                    "status": "completed",
                    "http_code": response.status_code,
                    "response_raw": response.text[:200]
                })
        except requests.exceptions.Timeout:
            print(f"❌ Request timed out")
            self.test_results.append({
                "test": "Payment Initiation",
                "status": "timeout"
            })
        except requests.exceptions.ConnectionError as e:
            print(f"❌ Connection error: {str(e)[:100]}")
            self.test_results.append({
                "test": "Payment Initiation",
                "status": "connection_error",
                "error": str(e)
            })
        except Exception as e:
            print(f"❌ Error: {str(e)}")
            self.test_results.append({
                "test": "Payment Initiation",
                "status": "error",
                "error": str(e)
            })
    
    def test_payment_inquiry(self):
        """Test payment inquiry API call"""
        print(f"\n{'='*70}")
        print("Testing Payment Inquiry (paymentsTxnInq)")
        print(f"{'='*70}")
        
        url = self.config["base_url"] + self.config["payment_inquiry_endpoint"]
        print(f"\n🌐 Endpoint: {url}")
        
        # Build inquiry payload
        payload = {
            "clientCode": self.config["client_code"],
            "custRef": f"TEST-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        }
        
        if self.config_name == "encrypted":
            try:
                encrypted_data = aes_encrypt(
                    json.dumps(payload, separators=(",", ":")),
                    self.config["encryption_key"],
                    self.config["encryption_iv"]
                )
                request_body = {
                    "paymentsTxnInqReq": {
                        "clientCode": self.config["client_code"],
                        "data": encrypted_data,
                    }
                }
            except Exception as e:
                print(f"❌ Encryption failed: {str(e)}")
                return
        else:
            request_body = {
                "paymentsTxnInqReq": {
                    "clientCode": self.config["client_code"],
                    "data": json.dumps(payload),
                }
            }
        
        print(f"\n📤 Request:")
        req_display = json.dumps(request_body, indent=2)
        print(req_display[:300] + ("..." if len(req_display) > 300 else ""))
        
        try:
            headers = {"Content-Type": "application/json"}
            response = requests.post(url, json=request_body, headers=headers, timeout=30, verify=False)
            
            print(f"\n📬 Response Status: HTTP {response.status_code}")
            
            try:
                response_data = response.json()
                print(f"📬 Response Body:")
                print(json.dumps(response_data, indent=2))
                self.test_results.append({
                    "test": "Payment Inquiry",
                    "status": "completed",
                    "http_code": response.status_code,
                    "response": response_data
                })
            except:
                print(f"📬 Response (raw):")
                print(response.text[:500])
                self.test_results.append({
                    "test": "Payment Inquiry",
                    "status": "completed",
                    "http_code": response.status_code,
                    "response_raw": response.text[:200]
                })
        except requests.exceptions.Timeout:
            print(f"❌ Request timed out")
            self.test_results.append({
                "test": "Payment Inquiry",
                "status": "timeout"
            })
        except requests.exceptions.ConnectionError:
            print(f"❌ Connection error")
            self.test_results.append({
                "test": "Payment Inquiry",
                "status": "connection_error"
            })
        except Exception as e:
            print(f"❌ Error: {str(e)}")
            self.test_results.append({
                "test": "Payment Inquiry",
                "status": "error",
                "error": str(e)
            })
    
    def run_all_tests(self):
        """Run all tests"""
        self.test_connectivity()
        self.test_encryption()
        if self.config_name == "encrypted":
            self.test_payment_initiation()
            self.test_payment_inquiry()
        else:
            print("\n⏭️  Skipping API tests (use encrypted profile for full testing)")
    
    def print_summary(self):
        """Print test summary"""
        print(f"\n{'='*70}")
        print("Test Summary")
        print(f"{'='*70}")
        for result in self.test_results:
            test_name = result.get("test", "Unknown")
            status = result.get("status", "unknown").upper()
            status_icon = "✅" if status in ["SUCCESS", "REACHABLE", "COMPLETED"] else "❌"
            print(f"{status_icon} {test_name:30} {status}")


# ============================================================================
# Main Test Runner
# ============================================================================

def run_bob_uat_tests():
    """Run all BoB UAT tests"""
    print("\n")
    print("╔" + "="*68 + "╗")
    print("║" + " "*68 + "║")
    print("║" + "Bank of Baroda (BoB) UAT Integration Test".center(68) + "║")
    print("║" + "Olive Platform - paymentTxn & paymentsTxnInq Endpoints".center(68) + "║")
    print("║" + " "*68 + "║")
    print("╚" + "="*68 + "╝")
    
    # Test both encrypted and unencrypted profiles
    for config_name in ["encrypted", "unencrypted"]:
        tester = BoBUATTester(config_name)
        tester.run_all_tests()
        tester.print_summary()
        print("\n")


if __name__ == "__main__":
    import sys
    import os
    
    # Disable SSL warnings for testing
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    run_bob_uat_tests()
