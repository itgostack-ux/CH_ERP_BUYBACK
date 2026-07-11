# Bank of Baroda (BoB) Olive Integration - Quick Setup Guide

## ✅ Status: Ready to Test

**API Endpoints Verified:**
- ✅ `https://apiuat.bankofbaroda.co.in:4443/olive/publisher/paymentTxn` (Reachable)
- ✅ `https://apiuat.bankofbaroda.co.in:4443/olive/publisher/paymentsTxnInq` (Reachable)

**Encryption Tested:**
- ✅ AES-256-CBC encryption working
- ✅ HMAC-SHA256 checksum computation working

---

## 🚀 Quick Start (5 Minutes)

### Step 1: Create Bank Integration Profile

```bash
cd /home/palla/erpnext-bench

# Run setup script (creates both encrypted and unencrypted profiles)
bench --site erpnext.local exec scripts/setup_bob_uat_profiles.py
```

**OR manually in ERPNext:**
1. Go to: Setup → Bank Integration Profile → New
2. Fill in the form:
   - Profile Name: `BoB Olive UAT - Encrypted`
   - Provider: `BoB DigiNext`
   - Active: ✓
   - Client Code: `059361561`
   - Base URL: `https://apiuat.bankofbaroda.co.in:4443`
   - Payment Initiation Path: `/olive/publisher/paymentTxn`
   - Inquiry Path: `/olive/publisher/paymentsTxnInq`
3. Save & Submit

### Step 2: Test Bank Connectivity

```bash
# Test UAT endpoints (encryption, checksum, API calls)
bench --site erpnext.local exec scripts/test_bob_uat.py
```

**Expected output:**
```
✅ Encryption & Checksum: SUCCESS
✅ Payment Initiation: COMPLETED
✅ Payment Inquiry: COMPLETED
```

### Step 3: Test Buyback Payment Workflow

```bash
# Run end-to-end test (creates test customer, order, initiates payment)
bench --site erpnext.local exec apps/buyback/test_bob_olive_e2e.py
```

**What it does:**
1. ✅ Verifies Bank Integration Profile
2. ✅ Creates test Buyback Order
3. ✅ Initiates payment via BoB API
4. ✅ Checks payment status
5. ✅ Retrieves payout details

### Step 4: Monitor in ERPNext

1. Go to: Setup → Bank Payment Request → List
2. Look for recently created requests
3. Check status: "Sent To Bank" (payment initiated)
4. Click to view CMS Ref and Bank Ref

---

## 📋 Test Credentials

### Encrypted Profile (Full Payment Flow)
```
Client Code:     059361561
Account:         04520200000401
Encryption Key:  BZGS536SHET4634234E4445434243444
Encryption IV:   cM5ApI1nitVect0r
Test Type:       Full payment with encryption
```

### Unencrypted Profile (For Comparison)
```
Client Code:     RACHANA
Account:         09570200000585
Test Type:       Simple JSON (no encryption)
```

---

## 🔧 Configuration Checklist

- [ ] Bank Integration Profile created
- [ ] Client Code configured correctly
- [ ] Encryption key stored (if using encrypted profile)
- [ ] Base URL: `https://apiuat.bankofbaroda.co.in:4443`
- [ ] Payment Initiation Path: `/olive/publisher/paymentTxn`
- [ ] Inquiry Path: `/olive/publisher/paymentsTxnInq`
- [ ] Test connectivity successful
- [ ] Test payment initiated successfully
- [ ] Status check working

---

## 📁 Files Created/Modified

```
BOB_OLIVE_INTEGRATION_GUIDE.md
  └─ Comprehensive integration guide with examples

apps/buyback/bob_uat_test.py
  └─ Test suite for encryption, checksum, and API calls

apps/buyback/bob_olive_integration.py
  └─ BoBOlivePayout class for orchestrating payments

scripts/setup_bob_uat_profiles.py
  └─ Setup script to create Bank Integration Profiles

scripts/test_bob_uat.py
  └─ Runnable test from bench console

apps/buyback/test_bob_olive_e2e.py
  └─ End-to-end test for full payment workflow
```

---

## 🧪 Testing Scenarios

### Scenario 1: Test Connectivity (2 minutes)
```bash
bench --site erpnext.local exec scripts/test_bob_uat.py
```
✅ Confirms endpoints are reachable
✅ Tests encryption/decryption
✅ Validates API request/response format

### Scenario 2: Create & Initiate Payment (5 minutes)
```bash
bench --site erpnext.local exec apps/buyback/test_bob_olive_e2e.py
```
✅ Creates test Buyback Order
✅ Initiates real payment via BoB API
✅ Gets CMS Ref & Bank Ref
✅ Checks initial status

### Scenario 3: Manual Payment in ERPNext UI

1. Go to: Buying → Buyback Order → New
2. Fill in:
   - Customer: Any customer
   - Item: Any item
   - Quantity: 1
   - Payout Mode: Bank Transfer
   - Bank Account: 1234567890123
   - IFSC: SBIN0001234
3. Save & Approve
4. In "Ready to Pay" state, click: "Initiate Payment"
5. System creates Bank Payment Request
6. Payment sent to BoB API
7. Status updates based on API response

---

## 📊 API Response Mapping

### Success Response (HTTP 200)
```json
{
  "paymentTxnResp": {
    "status": "S",
    "statusDesc": "Accepted",
    "cmsRef": "API2619200185763",
    "bankRef": "12345678901234567890",
    "UTR": "202607111234567890"
  }
}
```
→ Order status: "Sent To Bank" ✅

### Error Response (Missing Fields)
```json
{
  "paymentTxnResp": {
    "status": "F",
    "statusDesc": "Rejected by Bank",
    "errorCode": "TXN004",
    "errorDesc": "Mandatory/Conditional Mandatory fields not available ClientCode"
  }
}
```
→ Order status: "Failed" ❌

---

## 🔍 Troubleshooting

### Issue: Profile Creation Fails

**Error:** "Bank Integration Profile DocType not found"

**Solution:**
- Verify ch_payments app is installed
- Run: `bench list-apps | grep ch_payments`
- If missing: `bench get-app ch_payments`

---

### Issue: API Returns "Mandatory fields not available"

**Cause:** Encryption payload format incorrect

**Check:**
1. Verify ClientCode in plaintext payload matches profile
2. Ensure encryption uses correct key and IV
3. Check JSON field order (alphabetical for checksum)

---

### Issue: Connection Timeout

**Cause:** Network/firewall blocking port 4443

**Solution:**
```bash
# Test connectivity
curl -v https://apiuat.bankofbaroda.co.in:4443/olive/publisher/paymentTxn

# Check firewall allows HTTPS outbound
sudo iptables -L | grep 4443
```

---

### Issue: SSL Certificate Error

**For Testing (NOT production):**
```python
# In test script
requests.post(url, verify=False)
```

---

## 📞 Support Commands

### Check Payment Status
```bash
bench --site erpnext.local console

# Get recent Bank Payment Requests
frappe.db.get_list("Bank Payment Request", 
  fields=["name", "status", "cms_ref", "bank_ref", "transaction_amount"],
  limit=10)
```

### View Error Logs
```bash
# In ERPNext UI: Setup → System Console
# OR check file logs
tail -f logs/frappe.log
```

### Test Encryption Locally
```bash
# Run just the encryption test
python -c "
from apps.buyback.bob_uat_test import BoBUATTester
tester = BoBUATTester('encrypted')
tester.test_encryption()
tester.print_summary()
"
```

---

## 🎯 Next Phase: Production

### Before Going Live
1. ✅ Get production credentials from BoB
2. ✅ Create production Bank Integration Profile
3. ✅ Test all scenarios with production credentials
4. ✅ Verify SLA for payment settlement (usually 2-4 hours for NEFT)
5. ✅ Set up monitoring/alerts for failed payments
6. ✅ Train finance team on payment workflow

### Production Profile Setup
```bash
# Duplicate encrypted profile
# Rename: "BoB Olive UAT - Encrypted" → "BoB Olive Production"
# Update:
#   - Base URL: https://api.bankofbaroda.co.in (remove :4443)
#   - Client Code: [production code from BoB]
#   - Encryption Key: [production key from BoB]
#   - Checksum Key: [production key from BoB]
# Mark as Active
# Disable UAT profiles
```

---

## 📈 Performance Metrics

**Expected Payment Flow Times:**
- Payment Initiation: < 500ms (API call)
- Payment Processing: 2-4 hours (bank)
- Settlement: Next working day (NEFT)
- Status Update: Real-time (callback)

---

## 🔒 Security Checklist

- [ ] Encryption keys stored in secure password fields
- [ ] Checksum key never logged
- [ ] API credentials not in error messages
- [ ] Audit trail created for all payments
- [ ] IP whitelisting configured (if available)
- [ ] Rate limiting enabled
- [ ] SSL certificate validation enabled (production)
- [ ] Callbacks authenticated via checksum

---

## 📞 Getting Help

1. **For API Issues:** Contact BoB API Support
2. **For ERPNext Issues:** Check System Console (Setup → System Console)
3. **For Payment Status:** Use "Check Status" in Bank Payment Request
4. **For Debugging:** Enable debug logging (check log_level in config)

---

## 🎉 Success Indicators

✅ Bank Integration Profile created
✅ Test connectivity shows "COMPLETED"
✅ E2E test creates Bank Payment Request
✅ CMS Ref and Bank Ref returned from API
✅ Payment status shows "Sent To Bank"
✅ Customer receives funds within expected timeframe

**All green? You're ready to test buyback payments! 🚀**
