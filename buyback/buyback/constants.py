# Copyright (c) 2026, GoStack and contributors
# Buyback Status & Reporting Constants
# Single source of truth for all status values, SLA targets, and report config.
# Every report, dashboard, and scheduled job must import from here.

"""
Usage:
    from buyback.buyback.constants import PAID_STATUSES, SLA_TARGETS
"""

# ═══════════════════════════════════════════════════════════════════
# ORDER STATUS GROUPS
# ═══════════════════════════════════════════════════════════════════

PAID_STATUSES = ("Paid", "Closed")
TERMINAL_STATUSES = ("Closed", "Cancelled", "Rejected")
REJECTED_STATUSES = ("Rejected", "Cancelled")

# Orders still in-flight (not terminal)
ACTIVE_ORDER_STATUSES = (
    "Draft", "Awaiting Approval", "Approved",
    "Awaiting Customer Approval", "Customer Approved",
    "Awaiting OTP", "OTP Verified", "Ready to Pay", "Paid",
)

PENDING_APPROVAL_STATUSES = ("Awaiting Approval", "Awaiting Customer Approval")
PENDING_PAYMENT_STATUSES = ("Approved", "Customer Approved", "OTP Verified", "Ready to Pay", "Awaiting OTP")
SETTLED_STATUSES = ("Paid", "Closed")

# ═══════════════════════════════════════════════════════════════════
# INSPECTION STATUS GROUPS
# ═══════════════════════════════════════════════════════════════════

INSPECTION_OPEN_STATUSES = ("Draft", "In Progress")
INSPECTION_DONE_STATUSES = ("Completed",)

# ═══════════════════════════════════════════════════════════════════
# ASSESSMENT STATUS GROUPS
# ═══════════════════════════════════════════════════════════════════

ASSESSMENT_ACTIVE_STATUSES = ("Draft", "Submitted", "Inspection Created")
ASSESSMENT_TERMINAL_STATUSES = ("Expired", "Cancelled")

# ═════════════════════════════════════════════════════════════════
# ASSESSMENT SOURCES
# ═════════════════════════════════════════════════════════════════

SOURCE_APP = "App Diagnosis"
SOURCE_MANUAL = "Store Manual"
ASSESSMENT_SOURCES = (SOURCE_APP, SOURCE_MANUAL)

# ═══════════════════════════════════════════════════════════════════
# SETTLEMENT TYPES
# ═══════════════════════════════════════════════════════════════════

SETTLEMENT_BUYBACK = "Buyback"
SETTLEMENT_EXCHANGE = "Exchange"
SETTLEMENT_TYPES = (SETTLEMENT_BUYBACK, SETTLEMENT_EXCHANGE)

# ═══════════════════════════════════════════════════════════════════
# SLA TARGETS (minutes) — overridden by Buyback SLA Settings if present
# ═══════════════════════════════════════════════════════════════════

SLA_TARGETS = {
    "quote_to_inspection": 30,
    "inspection_to_link_sent": 10,
    "link_to_customer_approval": 60,
    "approval_to_settlement": 15,
    "payment_to_stock_entry": 30,
    "exchange_delivery_to_pickup": 2880,  # 48 hours
    "variance_approval": 20,
}

# ═══════════════════════════════════════════════════════════════════
# AGING BUCKETS (minutes)
# ═══════════════════════════════════════════════════════════════════

AGING_BUCKETS_MINUTES = [
    (0, 15, "0–15 min"),
    (15, 30, "15–30 min"),
    (30, 60, "30–60 min"),
    (60, None, "60+ min"),
]

AGING_BUCKETS_HOURS = [
    (0, 1, "0–1 hr"),
    (1, 4, "1–4 hr"),
    (4, 24, "4–24 hr"),
    (24, None, "24+ hr"),
]

# ═══════════════════════════════════════════════════════════════════
# AUDIT LOG ACTION PATTERNS (for LIKE queries)
# ═══════════════════════════════════════════════════════════════════

AUDIT_OVERRIDE_PATTERNS = ("Price Override", "Grade Changed")
AUDIT_APPROVAL_PATTERNS = ("Order Approved", "Customer Approved", "Customer Approval Requested")
AUDIT_SETTLEMENT_PATTERNS = ("Settlement Done", "Settlement Type Changed", "Payment Made")

# ═══════════════════════════════════════════════════════════════════
# REPORT FIELD REFERENCES (canonical field names for SQL queries)
# ═══════════════════════════════════════════════════════════════════

# These are the actual DB column names used in reports.
# If a field is renamed, update ONLY here and all reports pick it up.

ORDER_FIELDS = {
    "store": "store",
    "company": "company",
    "brand": "brand",
    "item_group": "item_group",
    "item": "item",
    "customer": "customer",
    "mobile_no": "mobile_no",
    "status": "status",
    "final_price": "final_price",
    "base_price": "base_price",
    "total_paid": "total_paid",
    "approved_price": "approved_price",
    "condition_grade": "condition_grade",
    "settlement_type": "settlement_type",
    "source": "source",  # assessment source field
    "customer_approved": "customer_approved",
    "price_variance_pct": "price_variance_pct",
    "imei_serial": "imei_serial",
}
