# Copyright (c) 2026, GoStack and contributors
# Demo Data Generator — Creates 30 days of realistic buyback test data

"""
Usage:
    bench --site erpnext.local execute buyback.buyback.demo_data.generate_demo_data

Generates:
    - 30 days of data across 5 branches
    - 20 device models × multiple grades
    - ~15-25 quotes/day/branch = ~2250+ quotes total
    - ~60-70% become orders, ~80% of those get paid
    - Mix of exceptions: high-variance, duplicate IMEI, rejections
    - Exchange orders for ~20% of paid orders
"""

import frappe
from frappe.utils import (
    nowdate, add_days, add_to_date, get_datetime,
    random_string, now_datetime, getdate,
)
import random
from datetime import datetime, timedelta


# ─── Configuration ───────────────────────────────────────────────

DEMO_BRANCHES = [
    "Stores - GOG-CHN-TNAGAR",
    "Stores - GOG-CHN-ANNA",
    "Stores - GOG-BLR-MG",
    "Stores - GOG-HYD-AMEERPET",
    "Stores - GOG-MUM-ANDHERI",
]

DEMO_BRANDS = ["Apple", "Samsung", "OnePlus", "Xiaomi", "Vivo"]

DEMO_MODELS = {
    "Apple": [
        ("iPhone 15 Pro Max", "Mobile Phone", 45000, 85000),
        ("iPhone 14 Pro", "Mobile Phone", 35000, 65000),
        ("iPhone 13", "Mobile Phone", 22000, 40000),
        ("iPad Air M2", "Tablet", 30000, 55000),
    ],
    "Samsung": [
        ("Galaxy S24 Ultra", "Mobile Phone", 40000, 75000),
        ("Galaxy S23", "Mobile Phone", 25000, 45000),
        ("Galaxy Z Flip5", "Mobile Phone", 30000, 55000),
        ("Galaxy Tab S9", "Tablet", 25000, 45000),
    ],
    "OnePlus": [
        ("OnePlus 12", "Mobile Phone", 28000, 50000),
        ("OnePlus 12R", "Mobile Phone", 18000, 32000),
        ("OnePlus Nord CE4", "Mobile Phone", 10000, 20000),
    ],
    "Xiaomi": [
        ("Xiaomi 14", "Mobile Phone", 22000, 40000),
        ("Redmi Note 13 Pro+", "Mobile Phone", 10000, 22000),
        ("Xiaomi Pad 6", "Tablet", 15000, 28000),
    ],
    "Vivo": [
        ("Vivo X100 Pro", "Mobile Phone", 25000, 48000),
        ("Vivo V30", "Mobile Phone", 15000, 28000),
    ],
}

GRADES = ["A", "B", "C", "D"]
GRADE_WEIGHTS = [0.25, 0.40, 0.25, 0.10]
GRADE_MULTIPLIERS = {"A": 1.0, "B": 0.85, "C": 0.70, "D": 0.50}

WARRANTY_OPTIONS = ["In Warranty", "Out of Warranty", "Unknown"]

STATUSES_FLOW = [
    "Draft", "Awaiting Approval", "Approved", "Awaiting OTP",
    "OTP Verified", "Ready to Pay", "Paid", "Closed",
]

PAYMENT_METHODS = ["Cash", "UPI", "Bank Transfer", "Cheque"]

DEMO_INSPECTORS = [
    "inspector1@example.com",
    "inspector2@example.com",
    "inspector3@example.com",
    "inspector4@example.com",
    "inspector5@example.com",
]

DEMO_EXECUTIVES = [
    "exec1@example.com",
    "exec2@example.com",
    "exec3@example.com",
    "exec4@example.com",
    "exec5@example.com",
]


def generate_demo_data(days=30, clear_existing=False):
    """Main entry point — generate demo data."""
    frappe.flags.in_demo_data = True

    if clear_existing:
        _clear_demo_data()

    # Ensure prerequisites exist
    _ensure_prerequisites()

    today = getdate(nowdate())
    start_date = add_days(today, -days)

    total_quotes = 0
    total_orders = 0
    total_exchanges = 0
    total_inspections = 0

    for day_offset in range(days):
        current_date = add_days(start_date, day_offset)
        is_weekend = getdate(current_date).weekday() >= 5

        for branch in DEMO_BRANCHES:
            # 15-25 quotes per branch on weekdays, 8-15 on weekends
            num_quotes = random.randint(8, 15) if is_weekend else random.randint(15, 25)

            for _ in range(num_quotes):
                try:
                    result = _create_transaction_chain(branch, current_date)
                    total_quotes += 1
                    if result.get("order"):
                        total_orders += 1
                    if result.get("exchange"):
                        total_exchanges += 1
                    if result.get("inspection"):
                        total_inspections += 1
                except Exception as e:
                    frappe.log_error(f"Demo data error: {e}")
                    continue

        # Commit every day to avoid memory issues
        frappe.db.commit()
        print(f"Day {day_offset + 1}/{days}: {current_date} done")

    frappe.flags.in_demo_data = False

    summary = (
        f"Demo data generated:\n"
        f"  Quotes: {total_quotes}\n"
        f"  Orders: {total_orders}\n"
        f"  Exchanges: {total_exchanges}\n"
        f"  Inspections: {total_inspections}\n"
        f"  Period: {start_date} to {today}\n"
        f"  Branches: {len(DEMO_BRANCHES)}"
    )
    print(summary)
    return summary


def _create_transaction_chain(branch, current_date):
    """Create a full transaction chain: quote → inspection → order → payment."""
    result = {"quote": None, "inspection": None, "order": None, "exchange": None}

    # Pick random model
    brand = random.choice(DEMO_BRANDS)
    model_name, category, min_price, max_price = random.choice(DEMO_MODELS[brand])
    grade = random.choices(GRADES, weights=GRADE_WEIGHTS, k=1)[0]
    warranty = random.choice(WARRANTY_OPTIONS)

    # Base price for this grade
    base = random.randint(min_price, max_price)
    base_price = round(base * GRADE_MULTIPLIERS[grade], -2)  # Round to nearest 100
    deduction_pct = random.uniform(0.05, 0.25)
    total_deductions = round(base_price * deduction_pct, -1)
    quoted_price = base_price - total_deductions
    imei = f"99{random.randint(1000000000000, 9999999999999)}"

    # Random time during business hours
    hour = random.randint(9, 20)
    minute = random.randint(0, 59)
    timestamp = f"{current_date} {hour:02d}:{minute:02d}:00"

    customer = _get_or_create_customer()
    executive = random.choice(DEMO_EXECUTIVES)

    # ─── Assessment ───
    assessment = frappe.new_doc("Buyback Assessment")
    assessment.customer = customer
    assessment.mobile_no = f"9{random.randint(100000000, 999999999)}"
    assessment.store = branch
    assessment.item_group = category
    assessment.brand = brand
    assessment.item = _get_or_create_item(model_name, brand, category)
    assessment.imei_serial = imei
    assessment.warranty_status = warranty
    assessment.estimated_price = quoted_price
    assessment.quoted_price = quoted_price
    assessment.status = "Submitted"
    assessment.source = random.choice(["App Diagnosis", "Store Manual"])
    assessment.owner = executive
    assessment.flags.ignore_permissions = True
    assessment.flags.ignore_mandatory = True
    assessment.insert(ignore_permissions=True)

    # Backdate creation
    frappe.db.set_value("Buyback Assessment", assessment.name, "creation", timestamp, update_modified=False)
    result["assessment"] = assessment.name

    # 30% of assessments expire/get cancelled
    if random.random() < 0.30:
        frappe.db.set_value("Buyback Assessment", assessment.name, "status",
                            random.choice(["Expired", "Cancelled"]))
        return result

    # Mark as Inspection Created
    frappe.db.set_value("Buyback Assessment", assessment.name, "status", "Inspection Created")

    # ─── Inspection ───
    insp_delay = random.randint(5, 45)  # 5-45 minutes after quote
    insp_start = add_to_date(timestamp, minutes=insp_delay)
    insp_duration = random.randint(3, 20)  # 3-20 minutes
    insp_end = add_to_date(insp_start, minutes=insp_duration)

    inspector = random.choice(DEMO_INSPECTORS)

    inspection = frappe.new_doc("Buyback Inspection")
    inspection.buyback_assessment = assessment.name
    inspection.store = branch
    inspection.customer = customer
    inspection.item = assessment.item
    inspection.brand = brand
    inspection.item_group = category
    inspection.imei_serial = imei
    inspection.inspector = inspector
    inspection.condition_grade = grade
    inspection.inspection_started_at = insp_start
    inspection.inspection_completed_at = insp_end
    inspection.status = "Completed"
    inspection.owner = inspector
    inspection.flags.ignore_permissions = True
    inspection.flags.ignore_mandatory = True
    inspection.insert(ignore_permissions=True)

    frappe.db.set_value("Buyback Inspection", inspection.name, "creation", insp_start, update_modified=False)
    result["inspection"] = inspection.name

    # 10% fail inspection
    if random.random() < 0.10:
        frappe.db.set_value("Buyback Inspection", inspection.name, "status", "Failed")
        return result

    # ─── Order ───
    order_delay = random.randint(2, 15)
    order_time = add_to_date(str(insp_end), minutes=order_delay)

    # Price variance simulation
    variance = 0
    requires_approval = 0
    if random.random() < 0.15:  # 15% have significant variance
        variance = random.uniform(-0.20, 0.20)
        requires_approval = 1

    final_price = round(quoted_price * (1 + variance), -1)

    # Pick a final status weighted realistically
    status_roll = random.random()
    if status_roll < 0.05:
        final_status = "Rejected"
    elif status_roll < 0.10:
        final_status = "Cancelled"
    elif status_roll < 0.15:
        final_status = "Awaiting Approval"
    elif status_roll < 0.20:
        final_status = "Approved"
    elif status_roll < 0.25:
        final_status = "Ready to Pay"
    elif status_roll < 0.85:
        final_status = "Paid"
    else:
        final_status = "Closed"

    # Duplicate IMEI simulation (2% chance)
    if random.random() < 0.02:
        imei = f"99{random.randint(1000000000, 9999999999)}000"  # Likely collision

    order = frappe.new_doc("Buyback Order")
    order.buyback_assessment = assessment.name
    order.buyback_inspection = inspection.name
    order.customer = customer
    order.mobile_no = f"9{random.randint(100000000, 999999999)}"
    order.store = branch
    order.item_group = category
    order.brand = brand
    order.item = quote.item
    order.imei_serial = imei
    order.condition_grade = grade
    order.warranty_status = warranty
    order.base_price = base_price
    order.total_deductions = total_deductions
    order.final_price = final_price
    order.requires_approval = requires_approval
    order.inspector = inspector
    order.status = final_status
    order.owner = executive
    order.flags.ignore_permissions = True
    order.flags.ignore_mandatory = True
    order.insert(ignore_permissions=True)

    frappe.db.set_value("Buyback Order", order.name, "creation", order_time, update_modified=False)

    # Set approval date for approved+ orders
    if final_status not in ("Draft", "Awaiting Approval", "Rejected", "Cancelled"):
        approval_delay = random.randint(5, 90)
        approval_date = add_to_date(str(order_time), minutes=approval_delay)
        frappe.db.set_value("Buyback Order", order.name, "approval_date", approval_date)

        if requires_approval:
            approver = random.choice(DEMO_EXECUTIVES)
            frappe.db.set_value("Buyback Order", order.name, "approved_by", approver)
            frappe.db.set_value("Buyback Order", order.name, "approved_price", final_price)

    # OTP verification for OTP+ statuses
    if final_status in ("OTP Verified", "Ready to Pay", "Paid", "Closed"):
        otp_delay = random.randint(1, 10)
        otp_time = add_to_date(str(order_time), minutes=random.randint(10, 30) + otp_delay)
        frappe.db.set_value("Buyback Order", order.name, "otp_verified_at", otp_time)
        frappe.db.set_value("Buyback Order", order.name, "otp_verified", 1)

    # Payment for paid/closed orders
    if final_status in ("Paid", "Closed"):
        pay_delay = random.randint(5, 30)
        pay_time = add_to_date(str(order_time), minutes=random.randint(30, 60) + pay_delay)
        payment_method = random.choice(PAYMENT_METHODS)

        frappe.db.set_value("Buyback Order", order.name, "total_paid", final_price)
        frappe.db.set_value("Buyback Order", order.name, "payment_status", "Paid")

        # Add payment child row
        try:
            order.reload()
            order.append("payments", {
                "payment_method": payment_method,
                "amount": final_price,
                "payment_date": pay_time,
                "transaction_reference": f"DEMO-{random_string(8).upper()}",
            })
            order.flags.ignore_permissions = True
            order.flags.ignore_mandatory = True
            order.save(ignore_permissions=True)
        except Exception:
            pass

    result["order"] = order.name

    # ─── Exchange Order (20% of paid orders) ───
    if final_status in ("Paid", "Closed") and random.random() < 0.20:
        try:
            exchange = _create_exchange_order(order, branch, customer, order_time)
            result["exchange"] = exchange
        except Exception:
            pass

    return result


def _create_exchange_order(order_doc, branch, customer, base_time):
    """Create an exchange order linked to a buyback order."""
    new_device_price = random.randint(20000, 100000)
    exchange_discount = random.randint(1000, 5000)

    ex_status_roll = random.random()
    if ex_status_roll < 0.1:
        ex_status = "New Device Delivered"
    elif ex_status_roll < 0.2:
        ex_status = "Awaiting Pickup"
    elif ex_status_roll < 0.7:
        ex_status = "Settled"
    else:
        ex_status = "Closed"

    delivery_time = add_to_date(str(base_time), hours=random.randint(1, 4))

    ex = frappe.new_doc("Buyback Exchange Order")
    ex.buyback_order = order_doc if isinstance(order_doc, str) else order_doc.name
    ex.customer = customer
    ex.store = branch
    ex.buyback_amount = frappe.db.get_value("Buyback Order", ex.buyback_order, "final_price") or 0
    ex.new_device_price = new_device_price
    ex.exchange_discount = exchange_discount
    ex.amount_to_pay = max(new_device_price - ex.buyback_amount - exchange_discount, 0)
    ex.status = ex_status
    ex.new_device_delivered_at = delivery_time
    ex.flags.ignore_permissions = True
    ex.flags.ignore_mandatory = True
    ex.insert(ignore_permissions=True)

    frappe.db.set_value("Buyback Exchange Order", ex.name, "creation", delivery_time, update_modified=False)

    if ex_status in ("Awaiting Pickup", "Settled", "Closed"):
        pickup_delay = random.randint(2, 72)  # 2-72 hours
        pickup_time = add_to_date(str(delivery_time), hours=pickup_delay)
        frappe.db.set_value("Buyback Exchange Order", ex.name, "old_device_received_at", pickup_time)

    if ex_status in ("Settled", "Closed"):
        settle_time = add_to_date(str(delivery_time), hours=random.randint(24, 96))
        frappe.db.set_value("Buyback Exchange Order", ex.name, "settlement_date", settle_time)

    return ex.name


# ─── Helper functions ────────────────────────────────────────────

_customer_cache = []

def _get_or_create_customer():
    """Get or create a demo customer."""
    global _customer_cache
    if not _customer_cache:
        existing = frappe.get_all("Customer", filters={"customer_group": "Individual"},
                                   fields=["name"], limit=50)
        _customer_cache = [c.name for c in existing]

    if _customer_cache and random.random() < 0.7:
        return random.choice(_customer_cache)

    # Create new customer
    name = f"Demo Customer {random_string(6)}"
    try:
        cust = frappe.new_doc("Customer")
        cust.customer_name = name
        cust.customer_group = "Individual"
        cust.territory = "India"
        cust.mobile_no = f"9{random.randint(100000000, 999999999)}"
        cust.flags.ignore_permissions = True
        cust.flags.ignore_mandatory = True
        cust.insert(ignore_permissions=True)
        _customer_cache.append(cust.name)
        return cust.name
    except Exception:
        if _customer_cache:
            return random.choice(_customer_cache)
        return None


_item_cache = {}

def _get_or_create_item(model_name, brand, category):
    """Get or create an item for the demo model."""
    global _item_cache
    cache_key = f"{brand} {model_name}"

    if cache_key in _item_cache:
        return _item_cache[cache_key]

    # Try to find existing
    existing = frappe.db.get_value("Item", {"item_name": model_name, "brand": brand}, "name")
    if existing:
        _item_cache[cache_key] = existing
        return existing

    # Also try by item_name alone
    existing = frappe.db.get_value("Item", {"item_name": model_name}, "name")
    if existing:
        _item_cache[cache_key] = existing
        return existing

    # Create item
    try:
        item = frappe.new_doc("Item")
        item.item_name = model_name
        item.item_group = category
        item.brand = brand
        item.is_stock_item = 1
        item.stock_uom = "Nos"
        item.flags.ignore_permissions = True
        item.flags.ignore_mandatory = True
        item.insert(ignore_permissions=True)
        _item_cache[cache_key] = item.name
        return item.name
    except Exception:
        return None


def _ensure_prerequisites():
    """Ensure demo users, warehouses, payment methods exist."""
    # Ensure demo users exist
    for user_email in DEMO_INSPECTORS + DEMO_EXECUTIVES:
        if not frappe.db.exists("User", user_email):
            try:
                user = frappe.new_doc("User")
                user.email = user_email
                user.first_name = user_email.split("@")[0].replace("_", " ").title()
                user.send_welcome_email = 0
                user.flags.ignore_permissions = True
                user.flags.ignore_password_policy = True
                user.new_password = "Demo@1234"
                user.insert(ignore_permissions=True)
            except Exception:
                pass

    # Ensure demo warehouses exist
    company = frappe.db.get_single_value("Global Defaults", "default_company") or "GoGizmo"
    for branch in DEMO_BRANCHES:
        if not frappe.db.exists("Warehouse", branch):
            try:
                wh = frappe.new_doc("Warehouse")
                wh.warehouse_name = branch.replace("Stores - ", "")
                wh.company = company
                wh.is_group = 0
                wh.ch_is_buyback_enabled = 1
                wh.flags.ignore_permissions = True
                wh.insert(ignore_permissions=True)
            except Exception:
                pass

    # Ensure payment methods exist
    for method in PAYMENT_METHODS:
        if not frappe.db.exists("Mode of Payment", method):
            try:
                mp = frappe.new_doc("Mode of Payment")
                mp.mode_of_payment = method
                mp.type = "Cash" if method == "Cash" else "Bank"
                mp.flags.ignore_permissions = True
                mp.insert(ignore_permissions=True)
            except Exception:
                pass


def _clear_demo_data():
    """Remove all demo data (use with caution!)."""
    print("Clearing demo data...")
    for dt in ["Buyback Exchange Order", "Buyback Order", "Buyback Inspection", "Buyback Assessment"]:
        frappe.db.sql(f"DELETE FROM `tab{dt}` WHERE owner LIKE 'exec%%@example.com'")
    frappe.db.commit()
    print("Demo data cleared.")
