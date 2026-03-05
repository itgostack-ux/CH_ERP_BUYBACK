"""
Buyback QA – Seed Factory
==========================
Idempotent creation of all master data needed by the 17 QA scenarios.

Usage::

    from buyback.qa.factory import seed_all, cleanup_all
    seed_all()           # idempotent
    cleanup_all()        # removes everything tagged QA-*
"""

from __future__ import annotations

import frappe
from frappe.utils import nowdate, add_days


# ─── Constants ────────────────────────────────────────────────────

QA_PREFIX = "QA-"

# Company & accounting
COMPANY = "GoGizmo Retail Pvt Ltd"
COMPANY_ABBR = "GGR"
CURRENCY = "INR"
QA_LOYALTY_PROGRAM = "QA Buyback Loyalty"

# ── Stores (3 branches) ──────────────────────────────────────────
STORES = [
    {"store_code": "QA-ANN", "store_name": "QA Anna Nagar", "city": "Chennai", "pincode": "600040"},
    {"store_code": "QA-KIL", "store_name": "QA Kilpauk", "city": "Chennai", "pincode": "600010"},
    {"store_code": "QA-VEL", "store_name": "QA Velachery", "city": "Chennai", "pincode": "600042"},
]

# ── Payment methods ──────────────────────────────────────────────
PAYMENT_METHODS = [
    {"method_name": "QA Cash", "method_type": "Cash"},
    {"method_name": "QA UPI", "method_type": "UPI", "requires_upi_id": 1},
    {"method_name": "QA Bank Transfer", "method_type": "Bank", "requires_bank_details": 1},
]

# ── Grades ────────────────────────────────────────────────────────
GRADES = [
    {"grade_name": "A", "display_order": 1, "description": "Excellent – no visible wear"},
    {"grade_name": "B", "display_order": 2, "description": "Good – minor cosmetic marks"},
    {"grade_name": "C", "display_order": 3, "description": "Fair – visible scratches or dents"},
    {"grade_name": "D", "display_order": 4, "description": "Poor – significant damage"},
]

# ── Customers ─────────────────────────────────────────────────────
CUSTOMERS = [
    {"customer_name": "QA Ravi Kumar", "mobile_no": "9876500001"},
    {"customer_name": "QA Priya Sharma", "mobile_no": "9876500002"},
    {"customer_name": "QA Ajay Singh", "mobile_no": "9876500003"},
    {"customer_name": "QA Deepa Nair", "mobile_no": "9876500004"},
    {"customer_name": "QA Fraud Tester", "mobile_no": "9876500099"},
]

# ── Items (20 device models across categories) ───────────────────
ITEMS = [
    # Smartphones (10)
    {"item_code": "QA-IPHONE-15", "item_name": "QA iPhone 15", "item_group": "Smartphones", "brand": "Apple", "market_price": 70000},
    {"item_code": "QA-IPHONE-14", "item_name": "QA iPhone 14", "item_group": "Smartphones", "brand": "Apple", "market_price": 55000},
    {"item_code": "QA-IPHONE-13", "item_name": "QA iPhone 13", "item_group": "Smartphones", "brand": "Apple", "market_price": 45000},
    {"item_code": "QA-SAM-S24", "item_name": "QA Samsung Galaxy S24", "item_group": "Smartphones", "brand": "Samsung", "market_price": 65000},
    {"item_code": "QA-SAM-S23", "item_name": "QA Samsung Galaxy S23", "item_group": "Smartphones", "brand": "Samsung", "market_price": 50000},
    {"item_code": "QA-SAM-A34", "item_name": "QA Samsung Galaxy A34", "item_group": "Smartphones", "brand": "Samsung", "market_price": 22000},
    {"item_code": "QA-PIX-8", "item_name": "QA Google Pixel 8", "item_group": "Smartphones", "brand": "Google", "market_price": 48000},
    {"item_code": "QA-ONE-12", "item_name": "QA OnePlus 12", "item_group": "Smartphones", "brand": "OnePlus", "market_price": 55000},
    {"item_code": "QA-OPPO-R12", "item_name": "QA Oppo Reno 12", "item_group": "Smartphones", "brand": "Oppo", "market_price": 30000},
    {"item_code": "QA-XI-14", "item_name": "QA Xiaomi 14", "item_group": "Smartphones", "brand": "Xiaomi", "market_price": 42000},
    # Laptops (5)
    {"item_code": "QA-MBP-M3", "item_name": "QA MacBook Pro M3", "item_group": "Laptops", "brand": "Apple", "market_price": 180000},
    {"item_code": "QA-MBA-M2", "item_name": "QA MacBook Air M2", "item_group": "Laptops", "brand": "Apple", "market_price": 100000},
    {"item_code": "QA-DELL-XPS", "item_name": "QA Dell XPS 15", "item_group": "Laptops", "brand": "Dell", "market_price": 120000},
    {"item_code": "QA-HP-SPEC", "item_name": "QA HP Spectre x360", "item_group": "Laptops", "brand": "HP", "market_price": 95000},
    {"item_code": "QA-LEN-TP", "item_name": "QA Lenovo ThinkPad X1", "item_group": "Laptops", "brand": "Lenovo", "market_price": 110000},
    # Tablets (5)
    {"item_code": "QA-IPAD-AIR", "item_name": "QA iPad Air", "item_group": "Tablets", "brand": "Apple", "market_price": 55000},
    {"item_code": "QA-IPAD-PRO", "item_name": "QA iPad Pro", "item_group": "Tablets", "brand": "Apple", "market_price": 90000},
    {"item_code": "QA-SAM-TABS9", "item_name": "QA Samsung Tab S9", "item_group": "Tablets", "brand": "Samsung", "market_price": 60000},
    {"item_code": "QA-LEN-TABP12", "item_name": "QA Lenovo Tab P12", "item_group": "Tablets", "brand": "Lenovo", "market_price": 30000},
    {"item_code": "QA-XI-PAD6", "item_name": "QA Xiaomi Pad 6", "item_group": "Tablets", "brand": "Xiaomi", "market_price": 25000},
]

# ── Question Bank (40 questions across categories) ───────────────
QUESTIONS = [
    # Condition questions (10)
    {"question_code": "QA-SCR-COND", "question_text": "Screen condition?", "question_type": "Single Select", "applies_to_category": "", "is_mandatory": 1, "display_order": 1,
     "options": [
         {"option_label": "Flawless", "option_value": "flawless", "price_impact_percent": 0},
         {"option_label": "Minor scratches", "option_value": "minor_scratch", "price_impact_percent": -5},
         {"option_label": "Cracked", "option_value": "cracked", "price_impact_percent": -25},
         {"option_label": "Dead pixels", "option_value": "dead_pixel", "price_impact_percent": -15},
     ]},
    {"question_code": "QA-BODY-COND", "question_text": "Body condition?", "question_type": "Single Select", "applies_to_category": "", "is_mandatory": 1, "display_order": 2,
     "options": [
         {"option_label": "Pristine", "option_value": "pristine", "price_impact_percent": 0},
         {"option_label": "Minor marks", "option_value": "minor_marks", "price_impact_percent": -3},
         {"option_label": "Dents", "option_value": "dents", "price_impact_percent": -10},
         {"option_label": "Cracked back", "option_value": "cracked_back", "price_impact_percent": -20},
     ]},
    {"question_code": "QA-BATT-HEALTH", "question_text": "Battery health above 80%?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 1, "display_order": 3,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -10},
     ]},
    {"question_code": "QA-CAM-WORK", "question_text": "Camera working?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 1, "display_order": 4,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -15},
     ]},
    {"question_code": "QA-TOUCH-OK", "question_text": "Touchscreen fully functional?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 1, "display_order": 5,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -20},
     ]},
    {"question_code": "QA-SPK-WORK", "question_text": "Speakers working?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 6,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -5},
     ]},
    {"question_code": "QA-MIC-WORK", "question_text": "Microphone working?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 7,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -5},
     ]},
    {"question_code": "QA-WIFI-BT", "question_text": "WiFi & Bluetooth working?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 8,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -8},
     ]},
    {"question_code": "QA-CHARGE-OK", "question_text": "Charging port functional?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 1, "display_order": 9,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -12},
     ]},
    {"question_code": "QA-FACEID", "question_text": "Face ID / Fingerprint working?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 10,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -8},
     ]},
    # Accessories questions (5)
    {"question_code": "QA-BOX-INC", "question_text": "Original box included?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 11,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -2},
     ]},
    {"question_code": "QA-CHARGER-INC", "question_text": "Charger included?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 12,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -1},
     ]},
    {"question_code": "QA-EARPH-INC", "question_text": "Earphones included?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 13,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -1},
     ]},
    {"question_code": "QA-CASE-INC", "question_text": "Protective case included?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 14,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": 0},
     ]},
    {"question_code": "QA-INVOICE-INC", "question_text": "Purchase invoice available?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 15,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -3},
     ]},
    # Lock / reset questions (5)
    {"question_code": "QA-ICLOUD-LOCK", "question_text": "iCloud / Google FRP locked?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 1, "display_order": 16,
     "options": [
         {"option_label": "No (unlocked)", "option_value": "no", "price_impact_percent": 0},
         {"option_label": "Yes (locked)", "option_value": "yes", "price_impact_percent": -100},
     ]},
    {"question_code": "QA-FACTORY-RESET", "question_text": "Factory reset done?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 17,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": 0},
     ]},
    {"question_code": "QA-SIMLOCK", "question_text": "SIM locked to carrier?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 18,
     "options": [
         {"option_label": "No (unlocked)", "option_value": "no", "price_impact_percent": 0},
         {"option_label": "Yes (locked)", "option_value": "yes", "price_impact_percent": -5},
     ]},
    {"question_code": "QA-WATER-DMG", "question_text": "Water damage indicators triggered?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 19,
     "options": [
         {"option_label": "No", "option_value": "no", "price_impact_percent": 0},
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": -30},
     ]},
    {"question_code": "QA-REPAIR-HIST", "question_text": "Previous repair history?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 20,
     "options": [
         {"option_label": "No repairs", "option_value": "no", "price_impact_percent": 0},
         {"option_label": "Third-party repair", "option_value": "yes", "price_impact_percent": -10},
     ]},
    # Laptop specific (10 more)
    {"question_code": "QA-KB-COND", "question_text": "Keyboard condition?", "question_type": "Single Select", "applies_to_category": "Laptops", "is_mandatory": 1, "display_order": 21,
     "options": [
         {"option_label": "All keys working", "option_value": "all_ok", "price_impact_percent": 0},
         {"option_label": "Some keys sticky", "option_value": "sticky", "price_impact_percent": -5},
         {"option_label": "Keys missing", "option_value": "missing", "price_impact_percent": -15},
     ]},
    {"question_code": "QA-TRACKPAD", "question_text": "Trackpad working?", "question_type": "Yes/No", "applies_to_category": "Laptops", "is_mandatory": 1, "display_order": 22,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -10},
     ]},
    {"question_code": "QA-PORT-COND", "question_text": "All ports functional?", "question_type": "Yes/No", "applies_to_category": "Laptops", "is_mandatory": 0, "display_order": 23,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -5},
     ]},
    {"question_code": "QA-HINGE-COND", "question_text": "Hinge condition?", "question_type": "Single Select", "applies_to_category": "Laptops", "is_mandatory": 0, "display_order": 24,
     "options": [
         {"option_label": "Tight and smooth", "option_value": "good", "price_impact_percent": 0},
         {"option_label": "Loose", "option_value": "loose", "price_impact_percent": -8},
         {"option_label": "Broken", "option_value": "broken", "price_impact_percent": -20},
     ]},
    {"question_code": "QA-RAM-SIZE", "question_text": "RAM size?", "question_type": "Single Select", "applies_to_category": "Laptops", "is_mandatory": 0, "display_order": 25,
     "options": [
         {"option_label": "16 GB+", "option_value": "16gb_plus", "price_impact_percent": 0},
         {"option_label": "8 GB", "option_value": "8gb", "price_impact_percent": -3},
         {"option_label": "4 GB", "option_value": "4gb", "price_impact_percent": -8},
     ]},
    # Tablet specific (5)
    {"question_code": "QA-PENCIL-WORK", "question_text": "Stylus / Pencil support working?", "question_type": "Yes/No", "applies_to_category": "Tablets", "is_mandatory": 0, "display_order": 26,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -5},
     ]},
    {"question_code": "QA-CELLULAR", "question_text": "Cellular model?", "question_type": "Yes/No", "applies_to_category": "Tablets", "is_mandatory": 0, "display_order": 27,
     "options": [
         {"option_label": "Yes (WiFi+Cell)", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No (WiFi only)", "option_value": "no", "price_impact_percent": -5},
     ]},
    # Generic / display-related extras (8 to reach 40)
    {"question_code": "QA-BURN-IN", "question_text": "Screen burn-in visible?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 28,
     "options": [
         {"option_label": "No", "option_value": "no", "price_impact_percent": 0},
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": -12},
     ]},
    {"question_code": "QA-POWER-BTN", "question_text": "Power button working?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 1, "display_order": 29,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -8},
     ]},
    {"question_code": "QA-VOL-BTN", "question_text": "Volume buttons working?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 30,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -3},
     ]},
    {"question_code": "QA-VIBRATE", "question_text": "Vibration motor working?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 31,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -2},
     ]},
    {"question_code": "QA-PROX-SENSOR", "question_text": "Proximity sensor working?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 32,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -3},
     ]},
    {"question_code": "QA-GPS-WORK", "question_text": "GPS working?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 33,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -3},
     ]},
    {"question_code": "QA-NFC-WORK", "question_text": "NFC working?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 34,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -2},
     ]},
    {"question_code": "QA-AUTOROTATE", "question_text": "Auto-rotate / gyroscope?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 35,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -2},
     ]},
    # Remaining to hit 40
    {"question_code": "QA-NETWORK-4G", "question_text": "4G / 5G bands working?", "question_type": "Yes/No", "applies_to_category": "Smartphones", "is_mandatory": 0, "display_order": 36,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -10},
     ]},
    {"question_code": "QA-DUAL-SIM", "question_text": "Dual SIM working?", "question_type": "Yes/No", "applies_to_category": "Smartphones", "is_mandatory": 0, "display_order": 37,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -3},
     ]},
    {"question_code": "QA-FLASH-WORK", "question_text": "Flash / Torch working?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 38,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -2},
     ]},
    {"question_code": "QA-STORAGE-CHECK", "question_text": "Storage health OK?", "question_type": "Yes/No", "applies_to_category": "", "is_mandatory": 0, "display_order": 39,
     "options": [
         {"option_label": "Yes", "option_value": "yes", "price_impact_percent": 0},
         {"option_label": "No", "option_value": "no", "price_impact_percent": -5},
     ]},
    {"question_code": "QA-COSMETIC-OVERALL", "question_text": "Overall cosmetic rating?", "question_type": "Single Select", "applies_to_category": "", "is_mandatory": 1, "display_order": 40,
     "options": [
         {"option_label": "Like new", "option_value": "like_new", "price_impact_percent": 0},
         {"option_label": "Good", "option_value": "good", "price_impact_percent": -3},
         {"option_label": "Average", "option_value": "average", "price_impact_percent": -8},
         {"option_label": "Below average", "option_value": "below_avg", "price_impact_percent": -15},
     ]},
]

# ── Checklist Templates (10) ─────────────────────────────────────
CHECKLISTS = [
    {"template_name": "QA Smartphone Full Check", "applies_to_category": "Smartphones",
     "items": [
         {"check_item": "Screen test", "check_code": "QA-CHK-SCR", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 1},
         {"check_item": "Touch test", "check_code": "QA-CHK-TOUCH", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 2},
         {"check_item": "Camera test", "check_code": "QA-CHK-CAM", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 3},
         {"check_item": "Battery check", "check_code": "QA-CHK-BATT", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 4},
         {"check_item": "Speaker test", "check_code": "QA-CHK-SPK", "check_type": "Pass/Fail", "is_mandatory": 0, "display_order": 5},
         {"check_item": "IMEI verification", "check_code": "QA-CHK-IMEI", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 6},
         {"check_item": "Cosmetic grade", "check_code": "QA-CHK-COSM", "check_type": "Grade (A/B/C/D)", "is_mandatory": 1, "display_order": 7},
     ]},
    {"template_name": "QA Smartphone Quick Check", "applies_to_category": "Smartphones",
     "items": [
         {"check_item": "Power on", "check_code": "QA-QCHK-PWR", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 1},
         {"check_item": "Screen test", "check_code": "QA-QCHK-SCR", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 2},
         {"check_item": "Quick cosmetic", "check_code": "QA-QCHK-COSM", "check_type": "Grade (A/B/C/D)", "is_mandatory": 1, "display_order": 3},
     ]},
    {"template_name": "QA Laptop Full Check", "applies_to_category": "Laptops",
     "items": [
         {"check_item": "Screen test", "check_code": "QA-LCHK-SCR", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 1},
         {"check_item": "Keyboard test", "check_code": "QA-LCHK-KB", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 2},
         {"check_item": "Trackpad test", "check_code": "QA-LCHK-TP", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 3},
         {"check_item": "Battery check", "check_code": "QA-LCHK-BATT", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 4},
         {"check_item": "Port test", "check_code": "QA-LCHK-PORT", "check_type": "Pass/Fail", "is_mandatory": 0, "display_order": 5},
         {"check_item": "Hinge check", "check_code": "QA-LCHK-HINGE", "check_type": "Pass/Fail", "is_mandatory": 0, "display_order": 6},
     ]},
    {"template_name": "QA Tablet Full Check", "applies_to_category": "Tablets",
     "items": [
         {"check_item": "Screen test", "check_code": "QA-TCHK-SCR", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 1},
         {"check_item": "Touch test", "check_code": "QA-TCHK-TOUCH", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 2},
         {"check_item": "Pencil test", "check_code": "QA-TCHK-PEN", "check_type": "Pass/Fail", "is_mandatory": 0, "display_order": 3},
         {"check_item": "Battery check", "check_code": "QA-TCHK-BATT", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 4},
     ]},
    {"template_name": "QA Apple Device Check", "applies_to_category": "",
     "items": [
         {"check_item": "iCloud lock check", "check_code": "QA-ACHK-ICLD", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 1},
         {"check_item": "Activation lock", "check_code": "QA-ACHK-ACT", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 2},
         {"check_item": "Find My status", "check_code": "QA-ACHK-FMY", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 3},
     ]},
    {"template_name": "QA Samsung Device Check", "applies_to_category": "",
     "items": [
         {"check_item": "FRP lock check", "check_code": "QA-SCHK-FRP", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 1},
         {"check_item": "Knox status", "check_code": "QA-SCHK-KNOX", "check_type": "Pass/Fail", "is_mandatory": 0, "display_order": 2},
     ]},
    {"template_name": "QA Accessory Check", "applies_to_category": "",
     "items": [
         {"check_item": "Box present", "check_code": "QA-ACCHK-BOX", "check_type": "Pass/Fail", "is_mandatory": 0, "display_order": 1},
         {"check_item": "Charger present", "check_code": "QA-ACCHK-CHG", "check_type": "Pass/Fail", "is_mandatory": 0, "display_order": 2},
         {"check_item": "Cable present", "check_code": "QA-ACCHK-CBL", "check_type": "Pass/Fail", "is_mandatory": 0, "display_order": 3},
     ]},
    {"template_name": "QA Water Damage Check", "applies_to_category": "",
     "items": [
         {"check_item": "LDI strip check", "check_code": "QA-WCHK-LDI", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 1},
         {"check_item": "Corrosion visible", "check_code": "QA-WCHK-CORR", "check_type": "Pass/Fail", "is_mandatory": 0, "display_order": 2},
     ]},
    {"template_name": "QA High Value Device Check", "applies_to_category": "",
     "items": [
         {"check_item": "Serial verification", "check_code": "QA-HVCHK-SER", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 1},
         {"check_item": "Photo documentation", "check_code": "QA-HVCHK-PHT", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 2},
         {"check_item": "Manager sign-off", "check_code": "QA-HVCHK-MGR", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 3},
     ]},
    {"template_name": "QA Minimal Check", "applies_to_category": "",
     "items": [
         {"check_item": "Power on test", "check_code": "QA-MNCHK-PWR", "check_type": "Pass/Fail", "is_mandatory": 1, "display_order": 1},
         {"check_item": "Visual inspection", "check_code": "QA-MNCHK-VIS", "check_type": "Grade (A/B/C/D)", "is_mandatory": 1, "display_order": 2},
     ]},
]

# ── Pricing Rules ─────────────────────────────────────────────────
PRICING_RULES = [
    {
        "rule_name": "QA Old Device Flat Deduction",
        "priority": 10,
        "rule_type": "Flat Deduction",
        "flat_deduction": 500,
        "min_age_months": 12,
        "max_age_months": 24,
    },
    {
        "rule_name": "QA Very Old Device Percentage",
        "priority": 20,
        "rule_type": "Percentage Deduction",
        "percent_deduction": 15,
        "min_age_months": 24,
    },
    {
        "rule_name": "QA Apple Brand Premium",
        "priority": 5,
        "rule_type": "Percentage Deduction",
        "percent_deduction": 2,
        "applies_to_brand": "Apple",
        "warranty_status": "Out of Warranty",
    },
    {
        "rule_name": "QA Slab-Based Deduction",
        "priority": 15,
        "rule_type": "Slab-Based",
        "applies_to_grade": None,
        "slabs": [
            {"from_amount": 0, "to_amount": 10000, "deduction_percent": 5},
            {"from_amount": 10001, "to_amount": 50000, "deduction_percent": 3},
            {"from_amount": 50001, "to_amount": 200000, "deduction_percent": 2},
        ],
    },
    {
        "rule_name": "QA D-Grade Penalty",
        "priority": 25,
        "rule_type": "Flat Deduction",
        "flat_deduction": 1000,
        "applies_to_grade": None,  # resolved at runtime to GRD for D
    },
]

# ── Test Users ────────────────────────────────────────────────────
TEST_USERS = [
    {"email": "qa_agent@test.com", "first_name": "QA", "last_name": "Agent", "roles": ["Buyback Agent"]},
    {"email": "qa_manager@test.com", "first_name": "QA", "last_name": "Manager", "roles": ["Buyback Manager"]},
    {"email": "qa_auditor@test.com", "first_name": "QA", "last_name": "Auditor", "roles": ["Buyback Auditor"]},
    {"email": "qa_admin@test.com", "first_name": "QA", "last_name": "Admin", "roles": ["Buyback Admin", "System Manager"]},
    {"email": "qa_store_mgr@test.com", "first_name": "QA", "last_name": "StoreMgr", "roles": ["Buyback Store Manager"]},
]


# ══════════════════════════════════════════════════════════════════
#  Public API
# ══════════════════════════════════════════════════════════════════

def seed_all(company: str | None = None):
    """Idempotently create ALL QA master data.  Returns summary dict."""
    co = company or COMPANY
    summary: dict[str, int] = {}
    frappe.flags.in_qa_seed = True
    try:
        summary["brands"] = _seed_brands()
        summary["item_groups"] = _seed_item_groups()
        summary["grades"] = _seed_grades()
        summary["stores"] = _seed_stores(co)
        summary["payment_methods"] = _seed_payment_methods()
        summary["customers"] = _seed_customers()
        summary["items"] = _seed_items()
        summary["price_masters"] = _seed_price_masters()
        summary["questions"] = _seed_questions()
        summary["checklists"] = _seed_checklists()
        summary["pricing_rules"] = _seed_pricing_rules()
        summary["loyalty_program"] = _seed_loyalty_program(co)
        summary["settings"] = _seed_settings(co)
        summary["users"] = _seed_users()
        summary["accounts"] = _seed_accounts(co)
        frappe.db.commit()
    finally:
        frappe.flags.in_qa_seed = False
    return summary


def cleanup_all():
    """Remove ALL QA-prefixed data.  Handles linked docs in correct order."""
    frappe.flags.in_qa_cleanup = True
    try:
        _cleanup_transactions()
        _cleanup_masters()
        _cleanup_users()
        frappe.db.commit()
    finally:
        frappe.flags.in_qa_cleanup = False


# ══════════════════════════════════════════════════════════════════
#  Seed helpers
# ══════════════════════════════════════════════════════════════════

def _seed_brands() -> int:
    """Create Brands with mandatory ch_manufacturers child table."""
    created = 0
    brand_names = {i["brand"] for i in ITEMS}
    for b in brand_names:
        # Ensure a Manufacturer exists with same name
        if not frappe.db.exists("Manufacturer", b):
            frappe.get_doc({
                "doctype": "Manufacturer",
                "short_name": b,
            }).insert(ignore_permissions=True)

        if not frappe.db.exists("Brand", b):
            doc = frappe.get_doc({
                "doctype": "Brand",
                "brand": b,
                "ch_manufacturers": [{"manufacturer": b}],
            })
            doc.insert(ignore_permissions=True)
            created += 1
    return created


def _seed_item_groups() -> int:
    created = 0
    groups = {i["item_group"] for i in ITEMS}
    for g in groups:
        if not frappe.db.exists("Item Group", g):
            frappe.get_doc({
                "doctype": "Item Group",
                "item_group_name": g,
                "parent_item_group": "All Item Groups",
            }).insert(ignore_permissions=True)
            created += 1
    return created


def _seed_grades() -> int:
    """Ensure A/B/C/D grades exist (idempotent — uses existing if found)."""
    created = 0
    for g in GRADES:
        if not frappe.db.exists("Grade Master", {"grade_name": g["grade_name"]}):
            frappe.get_doc({"doctype": "Grade Master", **g}).insert(ignore_permissions=True)
            created += 1
    return created


def _seed_stores(company: str) -> int:
    created = 0
    # Ensure a warehouse exists for the company
    default_wh = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
    for s in STORES:
        if not frappe.db.exists("CH Store", {"store_code": s["store_code"]}):
            frappe.get_doc({
                "doctype": "CH Store",
                "company": company,
                "warehouse": default_wh,
                "is_buyback_enabled": 1,
                **s,
            }).insert(ignore_permissions=True)
            created += 1
    return created


def _seed_payment_methods() -> int:
    created = 0
    for pm in PAYMENT_METHODS:
        if not frappe.db.exists("CH Payment Method", {"method_name": pm["method_name"]}):
            frappe.get_doc({"doctype": "CH Payment Method", **pm}).insert(ignore_permissions=True)
            created += 1
    return created


def _seed_customers() -> int:
    created = 0
    for c in CUSTOMERS:
        if not frappe.db.exists("Customer", {"customer_name": c["customer_name"]}):
            doc = frappe.get_doc({
                "doctype": "Customer",
                "customer_name": c["customer_name"],
                "customer_type": "Individual",
                "customer_group": "Individual",
                "territory": "India",
                "mobile_no": c["mobile_no"],
            })
            doc.insert(ignore_permissions=True)
            created += 1
        else:
            # Ensure mobile_no is set on existing customers
            name = frappe.db.get_value("Customer", {"customer_name": c["customer_name"]}, "name")
            if name and not frappe.db.get_value("Customer", name, "mobile_no"):
                frappe.db.set_value("Customer", name, "mobile_no", c["mobile_no"])
    return created


def _seed_items() -> int:
    # HSN codes by category (India Compliance)
    hsn_map = {
        "Smartphones": "85171300",
        "Laptops": "84713010",
        "Tablets": "84713090",
    }
    created = 0
    for item in ITEMS:
        if not frappe.db.exists("Item", item["item_code"]):
            doc = frappe.get_doc({
                "doctype": "Item",
                "item_code": item["item_code"],
                "item_name": item["item_name"],
                "item_group": item["item_group"],
                "brand": item["brand"],
                "stock_uom": "Nos",
                "is_stock_item": 1,
                "has_serial_no": 1,
                "serial_no_series": f"{item['item_code']}-.#####",
                "gst_hsn_code": hsn_map.get(item["item_group"], "85171300"),
            })
            doc.insert(ignore_permissions=True)
            created += 1
    return created


def _seed_price_masters() -> int:
    """Create Buyback Price Master for each QA item with tiered prices."""
    created = 0
    for item in ITEMS:
        if frappe.db.exists("Buyback Price Master", {"item_code": item["item_code"]}):
            continue
        mp = item["market_price"]
        doc = frappe.get_doc({
            "doctype": "Buyback Price Master",
            "item_code": item["item_code"],
            "item_name": item["item_name"],
            "is_active": 1,
            "current_market_price": mp,
            "vendor_price": round(mp * 0.5),
            # Grade A (best)
            "a_grade_iw_0_3": round(mp * 0.70),
            "b_grade_iw_0_3": round(mp * 0.60),
            "c_grade_iw_0_3": round(mp * 0.50),
            # IW 0-6
            "a_grade_iw_0_6": round(mp * 0.65),
            "b_grade_iw_0_6": round(mp * 0.55),
            "c_grade_iw_0_6": round(mp * 0.45),
            "d_grade_iw_0_6": round(mp * 0.30),
            # IW 6-11
            "a_grade_iw_6_11": round(mp * 0.60),
            "b_grade_iw_6_11": round(mp * 0.50),
            "c_grade_iw_6_11": round(mp * 0.40),
            "d_grade_iw_6_11": round(mp * 0.25),
            # OOW 11+
            "a_grade_oow_11": round(mp * 0.50),
            "b_grade_oow_11": round(mp * 0.40),
            "c_grade_oow_11": round(mp * 0.30),
            "d_grade_oow_11": round(mp * 0.15),
        })
        doc.flags.from_ready_reckoner = True
        doc.insert(ignore_permissions=True)
        created += 1
    return created


def _seed_questions() -> int:
    created = 0
    for q in QUESTIONS:
        if frappe.db.exists("Buyback Question Bank", {"question_code": q["question_code"]}):
            continue
        options = q.pop("options", [])
        doc = frappe.get_doc({"doctype": "Buyback Question Bank", **q})
        for opt in options:
            doc.append("options", opt)
        doc.insert(ignore_permissions=True)
        # Restore for re-runs
        q["options"] = options
        created += 1
    return created


def _seed_checklists() -> int:
    created = 0
    for cl in CHECKLISTS:
        if frappe.db.exists("Buyback Checklist Template", {"template_name": cl["template_name"]}):
            continue
        doc = frappe.get_doc({
            "doctype": "Buyback Checklist Template",
            "template_name": cl["template_name"],
            "applies_to_category": cl.get("applies_to_category", ""),
        })
        for item in cl["items"]:
            doc.append("items", item)
        doc.insert(ignore_permissions=True)
        created += 1
    return created


def _seed_pricing_rules() -> int:
    created = 0
    # Resolve D grade
    d_grade = frappe.db.get_value("Grade Master", {"grade_name": "D"}, "name")

    for pr in PRICING_RULES:
        if frappe.db.exists("Buyback Pricing Rule", {"rule_name": pr["rule_name"]}):
            continue
        data = {k: v for k, v in pr.items() if k != "slabs"}
        data["doctype"] = "Buyback Pricing Rule"
        # Resolve applies_to_grade for D-Grade Penalty
        if pr["rule_name"] == "QA D-Grade Penalty" and d_grade:
            data["applies_to_grade"] = d_grade
        doc = frappe.get_doc(data)
        for slab in pr.get("slabs", []):
            doc.append("slabs", slab)
        doc.insert(ignore_permissions=True)
        created += 1
    return created


def _seed_loyalty_program(company: str) -> int:
    """Create a Loyalty Program for QA buyback scenarios."""
    if frappe.db.exists("Loyalty Program", {"loyalty_program_name": QA_LOYALTY_PROGRAM}):
        return 0

    # Need an expense account for loyalty redemption
    expense_account = frappe.db.get_value(
        "Account",
        {"company": company, "account_type": "Expense Account", "is_group": 0},
        "name",
    )
    cost_center = frappe.db.get_value("Company", company, "cost_center")

    doc = frappe.get_doc({
        "doctype": "Loyalty Program",
        "loyalty_program_name": QA_LOYALTY_PROGRAM,
        "loyalty_program_type": "Single Tier Program",
        "auto_opt_in": 1,
        "from_date": nowdate(),
        "company": company,
        "expense_account": expense_account,
        "cost_center": cost_center,
        "conversion_factor": 1,
        "expiry_duration": 365,
        "collection_rules": [
            {
                "tier_name": "Base",
                "collection_factor": 10,  # 10 points per unit of currency threshold
                "minimum_total_spent": 0,
            },
        ],
    })
    doc.insert(ignore_permissions=True)
    return 1


def _seed_settings(company: str) -> int:
    """Configure Buyback Settings for QA."""
    settings = frappe.get_single("Buyback Settings")
    settings.default_company = company
    settings.default_currency = CURRENCY
    settings.quote_validity_days = 7
    settings.max_otp_attempts = 5
    settings.otp_expiry_minutes = 5
    settings.enable_auto_pricing = 1
    settings.price_rounding = "Round to nearest 10"
    settings.min_buyback_amount = 100
    settings.max_buyback_amount = 200000
    settings.require_manager_approval_above = 50000
    settings.require_otp_verification = 1

    # Loyalty settings
    lp_name = frappe.db.get_value("Loyalty Program", {"loyalty_program_name": QA_LOYALTY_PROGRAM})
    if lp_name:
        settings.enable_loyalty_points = 1
        settings.loyalty_program = lp_name
        settings.loyalty_points_per_100 = 10
        settings.loyalty_point_expiry_days = 365

    # Set expense account if available
    expense_acct = frappe.db.get_value(
        "Account",
        {"company": company, "account_type": "Expense Account", "is_group": 0},
        "name",
    )
    if expense_acct:
        settings.buyback_expense_account = expense_acct

    # Set stock account if available
    stock_acct = frappe.db.get_value(
        "Account",
        {"company": company, "account_type": "Stock", "is_group": 0},
        "name",
    )
    if stock_acct:
        settings.buyback_stock_account = stock_acct

    settings.save(ignore_permissions=True)
    return 1


def _seed_accounts(company: str) -> int:
    """Point buyback settings to existing expense & stock accounts."""
    updated = 0
    # Use existing Cost of Goods Sold for expense
    expense_acct = frappe.db.get_value(
        "Account",
        {"company": company, "account_name": "Cost of Goods Sold", "is_group": 0},
        "name",
    )
    if expense_acct:
        frappe.db.set_single_value("Buyback Settings", "buyback_expense_account", expense_acct)
        updated += 1

    # Use existing Stock In Hand for stock
    stock_acct = frappe.db.get_value(
        "Account",
        {"company": company, "account_name": "Stock In Hand", "is_group": 0},
        "name",
    )
    if stock_acct:
        frappe.db.set_single_value("Buyback Settings", "buyback_stock_account", stock_acct)
        updated += 1

    return updated


def _seed_users() -> int:
    created = 0
    _orig_in_test = frappe.flags.in_test
    frappe.flags.in_test = True          # bypass password-strength check
    try:
        for u in TEST_USERS:
            if not frappe.db.exists("User", u["email"]):
                user = frappe.get_doc({
                    "doctype": "User",
                    "email": u["email"],
                    "first_name": u["first_name"],
                    "last_name": u["last_name"],
                    "enabled": 1,
                    "user_type": "System User",
                    "send_welcome_email": 0,
                    "new_password": "QaT3st!Str0ng#2024",
                })
                for role in u["roles"]:
                    user.append("roles", {"role": role})
                user.insert(ignore_permissions=True)
                created += 1
    finally:
        frappe.flags.in_test = _orig_in_test
    return created


# ══════════════════════════════════════════════════════════════════
#  Cleanup helpers
# ══════════════════════════════════════════════════════════════════

def _cleanup_transactions():
    """Delete QA transactions in dependency order."""
    # Exchange Orders (cancel first if submitted)
    for name in frappe.get_all("Buyback Exchange Order", filters={"store": ["like", "QA-%"]}, pluck="name"):
        doc = frappe.get_doc("Buyback Exchange Order", name)
        if doc.docstatus == 1:
            doc.flags.ignore_links = True
            doc.cancel()
        frappe.delete_doc("Buyback Exchange Order", name, force=True, ignore_permissions=True)

    # Orders
    for name in frappe.get_all("Buyback Order", filters={"store": ["like", "QA-%"]}, pluck="name"):
        doc = frappe.get_doc("Buyback Order", name)
        if doc.docstatus == 1:
            doc.flags.ignore_links = True
            doc.cancel()
        frappe.delete_doc("Buyback Order", name, force=True, ignore_permissions=True)

    # Inspections
    for name in frappe.get_all("Buyback Inspection", filters={"store": ["like", "QA-%"]}, pluck="name"):
        frappe.delete_doc("Buyback Inspection", name, force=True, ignore_permissions=True)

    # Quotes
    for name in frappe.get_all("Buyback Quote", filters={"store": ["like", "QA-%"]}, pluck="name"):
        frappe.delete_doc("Buyback Quote", name, force=True, ignore_permissions=True)

    # Audit logs for QA docs
    for name in frappe.get_all("Buyback Audit Log", filters={"reference_name": ["like", "QA-%"]}, pluck="name"):
        frappe.delete_doc("Buyback Audit Log", name, force=True, ignore_permissions=True)

    # QA Test Runs
    for name in frappe.get_all("Buyback QA Test Run", pluck="name"):
        frappe.delete_doc("Buyback QA Test Run", name, force=True, ignore_permissions=True)

    # OTP Logs for QA mobiles
    for mobile in [c["mobile_no"] for c in CUSTOMERS]:
        for name in frappe.get_all("CH OTP Log", filters={"mobile_no": mobile}, pluck="name"):
            frappe.delete_doc("CH OTP Log", name, force=True, ignore_permissions=True)

    # Loyalty Point Entries for QA orders
    for name in frappe.get_all("Loyalty Point Entry", filters={"invoice_type": "Buyback Order"}, pluck="name"):
        frappe.delete_doc("Loyalty Point Entry", name, force=True, ignore_permissions=True)


def _cleanup_masters():
    """Delete QA master data."""
    # Pricing rules
    for name in frappe.get_all("Buyback Pricing Rule", filters={"rule_name": ["like", "QA %"]}, pluck="name"):
        frappe.delete_doc("Buyback Pricing Rule", name, force=True, ignore_permissions=True)

    # Checklists
    for name in frappe.get_all("Buyback Checklist Template", filters={"template_name": ["like", "QA %"]}, pluck="name"):
        frappe.delete_doc("Buyback Checklist Template", name, force=True, ignore_permissions=True)

    # Questions
    for name in frappe.get_all("Buyback Question Bank", filters={"question_code": ["like", "QA-%"]}, pluck="name"):
        frappe.delete_doc("Buyback Question Bank", name, force=True, ignore_permissions=True)

    # Price masters
    for name in frappe.get_all("Buyback Price Master", filters={"item_code": ["like", "QA-%"]}, pluck="name"):
        frappe.delete_doc("Buyback Price Master", name, force=True, ignore_permissions=True)

    # Items
    for item in ITEMS:
        if frappe.db.exists("Item", item["item_code"]):
            frappe.delete_doc("Item", item["item_code"], force=True, ignore_permissions=True)

    # Customers
    for c in CUSTOMERS:
        name = frappe.db.get_value("Customer", {"customer_name": c["customer_name"]})
        if name:
            frappe.delete_doc("Customer", name, force=True, ignore_permissions=True)

    # Stores
    for s in STORES:
        name = frappe.db.get_value("CH Store", {"store_code": s["store_code"]})
        if name:
            frappe.delete_doc("CH Store", name, force=True, ignore_permissions=True)

    # Payment methods
    for pm in PAYMENT_METHODS:
        name = frappe.db.get_value("CH Payment Method", {"method_name": pm["method_name"]})
        if name:
            frappe.delete_doc("CH Payment Method", name, force=True, ignore_permissions=True)

    # Loyalty Program
    lp = frappe.db.get_value("Loyalty Program", {"loyalty_program_name": QA_LOYALTY_PROGRAM})
    if lp:
        frappe.delete_doc("Loyalty Program", lp, force=True, ignore_permissions=True)


def _cleanup_users():
    """Delete QA test users."""
    for u in TEST_USERS:
        if frappe.db.exists("User", u["email"]):
            frappe.delete_doc("User", u["email"], force=True, ignore_permissions=True)


# ── Lookup helpers (used by scenarios) ────────────────────────────

def get_store(code: str = "QA-ANN") -> str:
    """Return CH Store name by store_code."""
    return frappe.db.get_value("CH Store", {"store_code": code}, "name")


def get_grade(letter: str = "A") -> str:
    """Return Grade Master name by grade_name."""
    return frappe.db.get_value("Grade Master", {"grade_name": letter}, "name")


def get_customer(name_fragment: str = "Ravi") -> str:
    """Return Customer name matching fragment."""
    return frappe.db.get_value("Customer", {"customer_name": ["like", f"%{name_fragment}%"]}, "name")


def get_payment_method(method_type: str = "Cash") -> str:
    """Return CH Payment Method name by method_type with QA prefix."""
    return frappe.db.get_value("CH Payment Method", {"method_name": ["like", "QA%"], "method_type": method_type}, "name")


def get_checklist(name_fragment: str = "Smartphone Full") -> str:
    """Return Buyback Checklist Template name matching fragment."""
    return frappe.db.get_value(
        "Buyback Checklist Template",
        {"template_name": ["like", f"%{name_fragment}%"]},
        "name",
    )


def get_loyalty_program() -> str:
    """Return QA Loyalty Program name."""
    return frappe.db.get_value(
        "Loyalty Program",
        {"loyalty_program_name": QA_LOYALTY_PROGRAM},
        "name",
    )
