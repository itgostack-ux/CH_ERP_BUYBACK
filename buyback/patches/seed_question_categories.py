"""Seed Buyback Question Category records from the old hardcoded Select options.

The question_category field was previously a Select with hardcoded values.
This patch creates master records so the new Link field resolves correctly.

Safety guards:
  - Skips if tabBuyback Question Category doesn't exist yet (means bench
    migrate hasn't synced the new DocType — will run on the next migrate).
  - Idempotent — skips categories that already exist.
  - Picks up any non-default values already saved in live data.
"""

import frappe

DEFAULT_CATEGORIES = [
    ("General",      "Default catch-all category"),
    ("Functional",   "Tests whether the device turns on and core functions work"),
    ("Physical",     "Checks for physical damage — dents, bends, broken parts"),
    ("Cosmetic",     "Screen, body, and cosmetic condition"),
    ("Accessories",  "Charger, box, earphones, and bundled items"),
    ("Software",     "OS version, unlocked status, and software health"),
    ("Battery",      "Battery health, charge cycle, and drainage tests"),
    ("Network",      "SIM, WiFi, Bluetooth, and connectivity checks"),
    ("Other",        "Miscellaneous questions not covered by other categories"),
]


def execute():
    # Guard: the table is created by bench migrate from our new DocType JSON.
    # On a fresh site the table may not exist on the first run — safe to skip.
    if not frappe.db.table_exists("tabBuyback Question Category"):
        frappe.logger("patch").warning(
            "seed_question_categories: tabBuyback Question Category does not exist yet — "
            "skipping (will run after next bench migrate)"
        )
        return

    existing = {
        r[0]
        for r in frappe.db.sql("SELECT name FROM `tabBuyback Question Category`")
    }

    # Collect values already stored in live question records
    in_use: set = set()
    if frappe.db.table_exists("tabBuyback Question Bank"):
        in_use = {
            r[0]
            for r in frappe.db.sql(
                "SELECT DISTINCT question_category FROM `tabBuyback Question Bank`"
                " WHERE question_category IS NOT NULL AND question_category != ''"
            )
        }

    default_map = {name: desc for name, desc in DEFAULT_CATEGORIES}
    all_categories = list(DEFAULT_CATEGORIES)
    for cat in in_use:
        if cat not in default_map:
            all_categories.append((cat, ""))

    inserted = 0
    for cat_name, description in all_categories:
        if cat_name in existing:
            continue
        try:
            frappe.get_doc({
                "doctype": "Buyback Question Category",
                "category_name": cat_name,
                "description": description,
                "disabled": 0,
            }).insert(ignore_permissions=True)
            inserted += 1
        except frappe.DuplicateEntryError:
            pass  # race — already created by another worker

    if inserted:
        frappe.logger("patch").info(
            "seed_question_categories: inserted %d category records", inserted
        )

    frappe.db.commit()
