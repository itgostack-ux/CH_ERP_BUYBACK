# Buyback mobile API

This is the supported API contract for the Buyback mobile applications. Responses use Frappe's standard envelope:

```json
{"message": {"name": "...", "status": "..."}}
```

Set `BASE_URL` to the site URL. Call methods at:

```
BASE_URL/api/method/<dotted-python-method>
```

For staff APIs, authenticate with a session or:

```
Authorization: token <api_key>:<api_secret>
Content-Type: application/json
```

Send POST arguments as JSON and GET arguments as query parameters. All endpoints require login, role, document, and store/company-scope permission unless marked **Public**.

## Intake and quote

| Method | Endpoint | Required parameters | Purpose |
|---|---|---|---|
| GET **Public** | `buyback.public_portal_api.search_buyback_items` | — | Eligible-device search; `query`, `limit` optional. |
| GET **Public** | `buyback.public_portal_api.get_quote_grades` | — | Non-salvage grades. |
| GET **Public** | `buyback.public_portal_api.get_public_quote_estimate` | `item_code` | Estimate; accepts grade, warranty/age and JSON `responses`. |
| POST **Public** | `buyback.public_portal_api.request_public_quote_otp` | `mobile_no` | Send quote OTP. |
| POST **Public** | `buyback.public_portal_api.submit_public_quote_request` | `customer_name`, `mobile_no`, `item_code`, `otp_code` | Submit verified public lead. |
| GET | `buyback.api.search_items` | — | Authenticated catalogue search. |
| GET | `buyback.api.check_device_quotable` | `item_code` | Check price-master eligibility. |
| GET | `buyback.api.lookup_imei_for_intake` | `imei` | Identify owned/external device and history. |
| GET | `buyback.api.get_reference_prices` | `item_code` | Price bands. |
| GET | `buyback.api.get_customer_questions_for_item` | `item_code` | Customer questions and options. |
| GET | `buyback.api.get_diagnostic_tests_for_item` | `item_code` | Diagnostic tests and options. |
| GET | `buyback.api.calculate_live_estimate` | `item_code` | Server-calculated quote from JSON diagnostics/responses. |
| POST | `buyback.api.create_assessment_from_intake` | `mobile_no`, `item_code` | Create/submit counter intake; accepts store, IMEI, warranty/age, JSON `diagnostics`/ `answers`. |
| GET | `buyback.api.get_estimate` | `item_code`, `grade` | Server-side estimate. |
| GET | `buyback.api.get_assessment` | `assessment_name` | Assessment details and answers. |
| POST | `buyback.api.submit_assessment` | `assessment_name` | Submit assessment. |
| POST | `buyback.api.submit_assessment_imei_validation` | `assessment_name`, `status` | Record assessment CEIR/IMEI validation. |
| POST | `buyback.api.submit_mobile_diagnostic` | `mobile_no`, `item_code`, `diagnostic_results` | Submit external mobile diagnostic results; store/IMEI/device attributes optional. |
| GET | `buyback.api.get_assessments_by_phone` | `mobile_no` | Assessment history. |

## Inspection and offer

| Method | Endpoint | Required parameters | Purpose |
|---|---|---|---|
| POST | `buyback.api.create_inspection_from_assessment` | `assessment_name` | Create inspection; optional checklist template. |
| POST | `buyback.api.create_inspection` | `assessment_name` | Alias for the preceding endpoint. |
| POST | `buyback.api.start_inspection` | `inspection_name` | Start inspection. |
| POST | `buyback.api.complete_inspection` | `inspection_name`, `condition_grade` | Complete; accepts revised price, JSON results, override reason. |
| GET | `buyback.api.get_inspections_by_phone` | `mobile_no` | Inspection history. |
| GET | `buyback.api.get_diagnostic_comparison` | `inspection_name` | Customer-vs-inspection diagnostics. |
| POST | `buyback.api.create_order` | customer, mobile, store, item, grade, final price | Create Buyback Order. |
| GET | `buyback.api.get_orders_by_phone` | `mobile_no` | Order history. |
| POST | `buyback.api.approve_order` | `order_name` | Internal approval. |
| POST | `buyback.api.reject_order` | `order_name` | Internal rejection. |
| POST | `buyback.api.customer_approve_offer` | `order_name` | In-store customer approval. |
| GET | `buyback.api.get_buyback_approval_details` | `token` | Token approval view; sensitive values masked. |
| POST | `buyback.api.customer_approve_via_token` | `token` | Token-based approval. |
| POST | `buyback.api.resend_customer_approval_link` | `order_name` | Reissue approval link. |
| POST | `buyback.api.request_price_exception` | `order_name`, `requested_price`, `reason` | Request price exception. |
| POST | `buyback.api.raise_buyback_exception` | `order`, `requested_price`, `reason` | Create routed price override. |

## KYC, settlement, and payout

| Method | Endpoint | Required parameters | Purpose |
|---|---|---|---|
| POST | `buyback.api.submit_imei_validation` | `order_name`, `status` | Record order CEIR/IMEI validation. |
| POST | `buyback.api.verify_kyc` | `order_name` | Verify KYC. |
| POST | `buyback.api.save_customer_payout_preference` | `token`, `payout_mode` | Save payout details before customer approval. |
| POST | `buyback.api.select_settlement_type` | `order_name`, `settlement_type` | Choose cash/exchange settlement. |
| POST | `buyback.api.send_otp` | `order_name` or `token` | Send order OTP. |
| POST | `buyback.api.verify_otp` | `otp_code` and order or token | Verify order OTP. |
| POST | `buyback.api.record_payment` | `order_name`, `payment_method`, `amount` | Record payment. |
| POST | `buyback.payment_api.initiate_payout` | `buyback_order` | Create bank payout request. |
| POST | `buyback.payment_api.approve_and_send_payout` | `bpr` | Submit/send payout. |
| GET | `buyback.payment_api.get_payout_status` | `buyback_order` | Current payout status. |
| POST | `buyback.payment_api.refresh_payout_status` | `buyback_order` | Refresh bank status. |
| GET | `buyback.payment_api.list_payouts` | `buyback_order` | Full payout history. |
| POST | `buyback.payment_api.retry_payout` | `buyback_order` | Retry failed/rejected payout. |
| POST | `buyback.api.close_order` | `order_name` | Close completed order. |

## Exchange, pickup, and wipe

| Method | Endpoint | Required parameters | Purpose |
|---|---|---|---|
| POST | `buyback.exchange_lifecycle.ensure_exchange_order_from_assessment` | `assessment_name` | Create/retrieve exchange order. |
| POST | `buyback.api.create_exchange` | order/customer/store/device/pricing fields | Create exchange directly. |
| POST | `buyback.api.advance_exchange` | `exchange_name`, `action` | Advance exchange workflow. |
| GET | `buyback.api.get_open_exchange_orders_for_customer` | `customer` | Open exchanges; mobile optional. |
| POST | `buyback.api.apply_exchange_to_invoice` | `exchange_order`, `sales_invoice` | Apply to invoice. |
| POST | `buyback.lifecycle_api.record_indemnity` | order, signer, signature type | Record indemnity. |
| POST | `buyback.lifecycle_api.schedule_pickup` | `order_name`, `appointment_date` | Schedule pickup; slot/address/assignee optional. |
| POST | `buyback.lifecycle_api.complete_pickup` | `appointment` | Complete pickup. |
| POST | `buyback.lifecycle_api.fail_pickup` | appointment, reason, next action | Record failed pickup. |
| POST | `buyback.lifecycle_api.reschedule_pickup` | appointment, date | Reschedule pickup. |
| POST | `buyback.lifecycle_api.record_data_wipe` | `order_name`, `wipe_method` | Record wipe/evidence. |
| POST | `buyback.lifecycle_api.verify_data_wipe` | `certificate` | Second-person verification. |

## Reference, operational, and management views

| Method | Endpoint | Required parameters | Purpose |
|---|---|---|---|
| GET | `buyback.api.get_questions` | — | Question bank; optional category. |
| GET | `buyback.api.get_question_options` | `question_name` | Question options. |
| GET | `buyback.api.get_grades` | — | Grade master; salvage optional. |
| GET | `buyback.api.get_stores` | — | Accessible Buyback stores. |
| GET | `buyback.api.get_payment_methods` | — | Payment methods. |
| GET | `buyback.api.get_imei_history` | `imei` | Consolidated IMEI lifecycle. |
| GET | `buyback.buyback.sla_engine.get_order_sla_summary` | `order_name` | Order SLA. |
| GET | `buyback.buyback.sla_engine.get_branch_sla_summary` | `store` | Branch SLA. |
| GET | `buyback.buyback.dashboard_api.get_store_dashboard` | — | Store dashboard filters. |
| GET | `buyback.buyback.dashboard_api.get_category_dashboard` | — | Category dashboard filters. |
| GET | `buyback.buyback.dashboard_api.get_finance_dashboard` | — | Finance dashboard filters. |
| GET | `buyback.buyback.dashboard_api.get_compliance_dashboard` | — | Compliance dashboard filters. |
| GET | `buyback.buyback.dashboard_api.get_operations_dashboard` | — | Operations dashboard filters. |
| GET | `buyback.buyback.page.buyback_hub.buyback_hub_api.get_buyback_hub_data` | — | Buyback Hub data. |
| GET | `buyback.buyback.scorecards.get_store_scorecards` | — | Store scorecards. |
| GET | `buyback.buyback.scorecards.get_inspector_scorecards` | — | Inspector scorecards. |
| GET | `buyback.buyback.scorecards.get_executive_scorecards` | — | Executive scorecards. |

## Boundaries and integration rules

Frappe also has generic resource URLs (for example, `/api/resource/Buyback Order`). They are not the mobile workflow contract: they can expose implementation fields and cannot replace the server-side workflow actions above.

- Server responses are authoritative: never calculate final price, grade, IMEI outcome, or workflow state on the device.
- Provide array arguments such as `responses`, `answers`, `diagnostics`, and `results` as JSON strings where accepted.
- Keep API secrets in a backend/mobile BFF where possible. Never ship Administrator credentials.
- Treat `403` as scope/role failure, `417` as validation failure, and `429` as rate-limited; display the server message.
