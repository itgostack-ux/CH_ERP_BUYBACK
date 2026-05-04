"""Seed Buyback Question Category records and migrate existing question_category values.

The question_category field was previously a Select with hardcoded options.
This patch:
  1. Creates a Buyback Question Category master record for every distinct value
     that already exists in tabBuyback Question Bank (preserving any custom
     data already saved there).
  2. Also seeds the default set of categories so fresh installs have them.
  3. The field values in tabBuyback Question Bank do NOT need to change —
     because autoname uses the category_name as the document name, so
     existing values like "General" already match their new master records.
"""

import frappe

DEFAULT_CATEGORIES = [
    ("General", "Default catch-all category"),
    ("Functional", "Tests whether the device turns on and core functions work"),
    ("Physical", "Checks for physical damage — dents, bends, broken parts"),
    ("Cosmetic", "Screen, body, and cosmetic condition"),
    ("Accessories", "Charger, box, earphones, and bundled items"),
    ("Software", "OS version, unlocked status, and software health"),
    ("Battery", "Battery health, charge cycle, and drainage tests"),
    ("Network", "SIM, WiFi, Bluetooth, and connectivity checks"),
    ("Other", "Miscellaneous questions not covered by other categories"),
]


def execute():
    existing_names = {
        r[0] for r in frappe.db.sql(
            "SELECT name FROM `tabBuyback Question Category`"
        )
    }

    # Collect categories currently in use by questions
    in_use = {
        r[0] for r in frappe.db.sql(
            "SELECT DISTINCT question_category FROM `tabBuyback Question Bank` "
            "WHERE question_category IS NOT NULL AND question_category != ''"
        )
    }

    # Build unified list: defaults + anything found in live data
    default_map = {name: desc for name, desc in DEFAULT_CATEGORIES}
    all_categories = list(DEFAULT_CATEGORIES)
    for cat in in_use:
        if cat not in default_map:
            all_categories.append((cat, ""))

    for cat_name, description in all_categories:
        if cat_name in existing_names:
            continue
        doc = frappe.get_doc({
            "doctype": "Buyback Question Category",
            "category_name": cat_name,
            "description": description,
            "disabled": 0,
        })
        doc.insert(ignore_permissions=True)

    frappe.db.commit()
