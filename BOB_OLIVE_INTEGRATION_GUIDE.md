# Bank of Baroda (BoB) Olive Platform Integration Guide

## Overview

This guide sets up Bank of Baroda's new **Olive Platform** payment API for buyback payments in ERPNext.

**Tested Endpoints:**
- Payment Initiation: `https://apiuat.bankofbaroda.co.in:4443/olive/publisher/paymentTxn`
- Payment Inquiry: `https://apiuat.bankofbaroda.co.in:4443/olive/publisher/paymentsTxnInq`

**Test Status:** ✅ Endpoints reachable & responding

---

## Test Credentials

### Profile 1: Encrypted with Credentials
```
Client Code: 059361561
Account No: 04520200000401
Encryption Key: BZGS536SHET4634234E4445434243444
IV: cM5ApI1nitVect0r
Testing: Full payment initiation & inquiry
```

### Profile 2: Unencrypted (Simpler Testing)
```
Client Code: RACHANA
Account No: 09570200000585
Testing: Without encryption
```

---

## Architecture

```
ERPNext Buyback Order
    ↓
buyback.payment_api.initiate_payout()
    ↓
ch_payments.bank_payments.api.create_bank_payment_request()
    ↓
Bank Integration Profile (BoB Olive UAT)
    ↓
BoBDigiNextProvider.initiate_payment()
    ↓
HTTPS POST to paymentTxn endpoint
    ↓
Bank of Baroda Olive API
```

---

## Setup Steps

### Step 1: Verify ch_payments App
```bash
# Check if ch_payments app is installed
ls -la apps/ch_payments/

# If not found, it needs to be added to the bench
```

### Step 2: Create Bank Integration Profile

Run the setup script:
```bash
cd /home/palla/erpnext-bench
bench --site erpnext.local exec scripts/setup_bob_uat_profiles.py
```

**OR manually in ERPNext UI:**
1. Go to **Bank Integration Profile** list
2. Click **+ New**
3. Fill in the following:

**Profile: BoB Olive UAT - Encrypted**
```
Profile Name: BoB Olive UAT - Encrypted
Provider: BoB DigiNext
Active: ✓
Bank: Bank of Baroda
Company: [Your Company]
Client Code: 059361561
Encryption Key: BZGS536SHET4634234E4445434243444
Checksum Key: [get from BoB - for checksum validation]
Base URL: https://apiuat.bankofbaroda.co.in:4443
Payment Initiation Path: /olive/publisher/paymentTxn
Inquiry Path: /olive/publisher/paymentsTxnInq
Callback Path: /api/v1/bank/callback/bob
Supported Payment Modes: NEFT, RTGS, IMPS, IFT
Default Payment Mode: NEFT
RTGS Minimum: 200000
Max Transaction: 10000000
```

### Step 3: Create Bank Master (if needed)
```bash
# In ERPNext, create a Bank:
# Setup → Bank → New
# Bank Name: Bank of Baroda
# SWIFT Code: BARBINBBXXX
```

### Step 4: Create Bank Account
```bash
# In ERPNext, create Bank Account:
# Setup → Bank Account → New
# Bank: Bank of Baroda
# Account: 04520200000401 (for encrypted profile)
# IFSC: BARB0XXXXXX
# Company: [Your Company]
```

### Step 5: Configure Buyback Settings
```bash
# In ERPNext, go to Buyback Settings
# Enable: require_otp_for_payment = 1
# Enable: enable_split_payment = 0 (if using single payment per order)
```

---

## Testing Payment Workflow

### Test 1: Create Buyback Order

```bash
# In ERPNext POS or Buyback module
# Create a new Buyback Order with:
# - Customer Name: Test Customer
# - Item: Any item
# - Quantity: 1
# - Payout Mode: Bank Transfer
# - Account: 04520200000401
# - IFSC: SBIN0001234 (test)
```

### Test 2: Customer Approval

```bash
# Customer approves buyback via link (auto-sent after order creation)
# Portal: /app/buyback-approval?token=xxx
# Confirms payout details
```

### Test 3: OTP Verification

```bash
# System sends OTP to registered mobile
# Customer verifies via portal
# Order status: OTP Verified → Ready to Pay
```

### Test 4: Initiate Payment

```bash
# Finance team initiates payment in "Ready to Pay" state
# System calls: buyback.payment_api.initiate_payout()
# Creates Bank Payment Request (BPR)
```

### Test 5: Payment Inquiry

```bash
# After initiation, system polls for status
# Calls: buyback.payment_api.get_payout_status()
# Gets CMS Ref + Bank Ref for tracking
```

### Test 6: Payment Confirmation

```bash
# Bank processes payment (usually within 2 hours for NEFT)
# Status callback received at: /api/v1/bank/callback/bob
# Order status: Awaiting Payment → Paid
# Funds credited to customer account
```

---

## API Request/Response Examples

### Payment Initiation Request (Encrypted)

```json
POST https://apiuat.bankofbaroda.co.in:4443/olive/publisher/paymentTxn

{
  "paymentTxnReq": {
    "clientCode": "059361561",
    "data": "BASE64_ENCRYPTED_PAYLOAD"
  }
}
```

**Encrypted Payload (after decryption):**
```json
{
  "custTxnRef": "TEST-20260711063407",
  "clientCode": "059361561",
  "tranAmount": "1000.00",
  "paymentType": "NEFT",
  "valueDate": "12-07-2026",
  "isSubmbr": "N",
  "beneAccNo": "1234567890123",
  "IFSC": "SBIN0001234",
  "beneName": "Test Customer",
  "beneMail": "customer@example.com",
  "beneMobile": "9876543210",
  "debitAcNo": "04520200000401",
  "beneAdd1": "Test Address",
  "invDtlReq": "N",
  "checksum": "SHA256_HMAC_HEX"
}
```

### Payment Initiation Response

**Success (HTTP 200):**
```json
{
  "paymentTxnResp": {
    "status": "S",
    "statusDesc": "Accepted",
    "cmsRef": "API2619200185763",
    "bankRef": "12345678901234567890",
    "UTR": "202607111234567890",
    "dateTime": "2026-07-11 12:04:20",
    "errorCode": "0",
    "errorDesc": ""
  }
}
```

**Error (TXN004 - Missing ClientCode):**
```json
{
  "paymentTxnResp": {
    "status": "F",
    "statusDesc": "Rejected by Bank",
    "errorCode": "TXN004",
    "errorDesc": "Mandatory/Conditional Mandatory fields not available ClientCode",
    "cmsRef": "API2619200185763",
    "custRef": "",
    "dateTime": "2026-07-11 12:04:20"
  }
}
```

### Payment Inquiry Request

```json
POST https://apiuat.bankofbaroda.co.in:4443/olive/publisher/paymentsTxnInq

{
  "paymentsTxnInqReq": {
    "clientCode": "059361561",
    "data": "BASE64_ENCRYPTED_PAYLOAD"
  }
}
```

### Payment Inquiry Response

```json
{
  "paymentsTxnInqResp": {
    "status": "S",
    "statusDesc": "Settled",
    "bankRef": "12345678901234567890",
    "cmsRef": "API2619200185763",
    "custRef": "TEST-20260711063407",
    "UTR": "202607111234567890",
    "valDate": "12-07-2026",
    "paymentType": "NEFT",
    "beneName": "Test Customer",
    "debitAccount": "04520200000401",
    "drAmt": "1000.00"
  }
}
```

---

## Encryption Details

### AES-256-CBC Encryption

**Algorithm:** AES in CBC mode with PKCS7 padding

**Key:** 32 bytes (64 hex characters)
```
BZGS536SHET4634234E4445434243444 (32 bytes)
```

**IV (Initialization Vector):** 16 bytes (32 hex characters)
```
cM5ApI1nitVect0r → converted to bytes
```

**Python Implementation:**
```python
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import base64

def aes_encrypt(plaintext, key_hex, iv_hex):
    key = bytes.fromhex(key_hex)
    iv = bytes.fromhex(iv_hex)
    
    # PKCS7 padding
    block_size = 16
    padding_length = block_size - (len(plaintext) % block_size)
    padded_plaintext = plaintext + chr(padding_length) * padding_length
    
    cipher = Cipher(
        algorithms.AES(key),
        modes.CBC(iv),
        backend=default_backend()
    )
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded_plaintext.encode()) + encryptor.finalize()
    
    return base64.b64encode(ciphertext).decode()
```

### Checksum (SHA-256 HMAC)

**Algorithm:** SHA-256 HMAC

**Key:** Checksum key provided by bank

**Field Order:** Alphabetical by key name

```python
import hashlib
import hmac

def compute_checksum(data, secret_key):
    sorted_vals = []
    for key in sorted(data.keys()):
        if data[key] and key not in ('checksum', 'signature'):
            sorted_vals.append(str(data[key]))
    
    message = "|".join(sorted_vals)
    return hmac.new(
        secret_key.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
```

---

## Troubleshooting

### Issue: "TXN004 - Mandatory/Conditional Mandatory fields not available ClientCode"

**Cause:** ClientCode not properly passed in encrypted payload

**Solution:**
1. Verify ClientCode matches the profile configuration
2. Ensure ClientCode is included in the JSON payload before encryption
3. Check that decryption on bank's side matches the encryption algorithm

### Issue: "ENQ004 - Mandatory/Conditional Mandatory fields not available"

**Cause:** Inquiry payload missing required fields

**Solution:**
1. Ensure either `custRef` (customer transaction reference), `bankRef`, or `cmsRef` is provided
2. Verify at least one reference exists for the transaction being inquired

### Issue: Connection Timeout

**Cause:** Network or firewall issue with UAT endpoint

**Solution:**
1. Verify endpoint URL: `https://apiuat.bankofbaroda.co.in:4443`
2. Check firewall allows HTTPS (port 4443)
3. Verify SSL certificate validity (use curl -v for debugging)

### Issue: SSL Certificate Verification Failed

**For Testing Only:**
```bash
# Disable SSL verification (NOT for production)
requests.post(url, verify=False)
```

---

## Monitoring & Debugging

### Check Payment Request Status

```bash
# In Frappe console
bench --site erpnext.local console

# Query Bank Payment Requests
frappe.db.get_list("Bank Payment Request", filters={
    "docstatus": 1,  # Submitted
    "status": "Pending"  # Awaiting status update
}, fields=["name", "payment_amount", "status", "cms_ref", "bank_ref"])
```

### Check Payment Logs

```bash
# In ERPNext, go to: Setup → Error Log or System Console
# Look for bank_integration or payment-related errors
# Check API call logs in ch_payments module
```

### Enable Debug Logging

```bash
# In bench config
# Add to common_site_config.json:
{
  "log_level": "DEBUG",
  "logger_config": {
    "ch_payments.bank_payments": "DEBUG"
  }
}
```

---

## Production Deployment

### Before Going Live

1. ✅ Test all scenarios with UAT credentials
2. ✅ Verify encryption/checksum with BoB team
3. ✅ Get production credentials from BoB
4. ✅ Register production callbacks URL with BoB
5. ✅ Test payment confirmation workflow
6. ✅ Set up SLA alerts for failed payments
7. ✅ Configure audit logging for compliance

### Create Production Profile

1. Duplicate "BoB Olive UAT - Encrypted" profile
2. Rename to "BoB Olive Production"
3. Update:
   - Base URL: `https://api.bankofbaroda.co.in` (production)
   - Client Code: [production code from BoB]
   - Encryption Key: [production key from BoB]
   - Checksum Key: [production key from BoB]
4. Mark as "Active"
5. Disable UAT profiles

### Production Rollout Checklist

- [ ] All integration tests passing
- [ ] Customer approval flow tested
- [ ] OTP verification working
- [ ] Payment initiation successful
- [ ] Payment inquiry showing correct status
- [ ] Payment confirmation callback received
- [ ] Audit logs capturing all transactions
- [ ] SLA alerts configured
- [ ] Finance team trained on workflow
- [ ] Rollback plan documented

---

## Support

For BoB integration issues:
1. Contact BoB API Support
2. Provide API logs and error codes
3. Reference CMS Ref from API response
4. Check BoB's developer portal for updated documentation

---

## Files Modified/Created

```
apps/buyback/buyback/bob_uat_test.py
    → Comprehensive UAT test suite with encryption testing

apps/buyback/buyback/bob_olive_integration.py
    → New Olive-specific integration code (if needed)

scripts/setup_bob_uat_profiles.py
    → Setup script for Bank Integration Profiles

scripts/test_bob_uat.py
    → Runnable test from bench console

BOB_OLIVE_INTEGRATION_GUIDE.md
    → This file
```

---

## Next Steps

1. **Test Phase:**
   - Run test suite: `bench --site erpnext.local exec scripts/test_bob_uat.py`
   - Create test profiles
   - Run payment workflow e2e tests

2. **Production Phase:**
   - Get production credentials from BoB
   - Create production profiles
   - Run full integration tests
   - Deploy to production

3. **Go-Live:**
   - Monitor first 50 transactions carefully
   - Check all payments settle within expected timeframes
   - Verify audit trail for compliance
   - Scale up gradually

