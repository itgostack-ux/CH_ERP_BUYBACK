"""Remove the warranty double-charge and price the two faults stuck at 0%.

Two live data problems, both of which made the quote wrong in a way no screen
showed:

1. "Is your device under manufacturer Warrenty?" deducted **72%** for a No.
   But warranty status already picks which column of the Buyback Price Master
   is used — an out-of-warranty device is priced from the OOW band, which is
   lower by construction. Deducting again charged the customer twice for the
   same fact. The question stays (it records the answer, and the answer feeds
   warranty_status) but its price impact goes to zero.

2. "GPS" and "Flash light" were mapped to every device, asked of every
   customer, and configured at 0% — so a genuinely broken GPS or flash
   deducted nothing. Unlike the other zero-impact tests, neither has a
   customer-question twin to inherit a rate from once fault grouping is in
   place, so they need a value here. Both take the Android rate from the
   depreciation sheet; the Apple rates arrive with the brand-family split.

Deliberately NOT corrected here: the ~20 other rates that disagree with the
sheet (bluetooth at 20% against a specified 5%, camera glass at 0.5% against
3%, Face ID at 10% against 25%). Those are rewritten wholesale, with Apple and
Android rates, when the catalogue is re-seeded — doing half of it now would
leave the data in a state neither this patch nor that one describes.

Idempotent.
"""

import frappe

# question_code → {option_value: new price_impact_percent}
CORRECTIONS = {
    # Warranty is priced by the band, not by a deduction.
    "manufacturer_warrenty": {"No": 0, "Yes": 0},
    # Android rates from the depreciation sheet (GPS 2%, Flash 7%).
    "gps": {"Fail": 2},
    "flash_light": {"Fail": 7},
}

REASONS = {
    "manufacturer_warrenty": "warranty already selects the price band",
    "gps": "was 0% — a broken GPS deducted nothing",
    "flash_light": "was 0% — a broken flash deducted nothing",
}


def execute():
    if not frappe.db.table_exists("Buyback Question Option"):
        return

    changed = 0
    for question_code, option_map in CORRECTIONS.items():
        questions = frappe.get_all(
            "Buyback Question Bank",
            filters={"question_code": question_code},
            pluck="name",
        )
        if not questions:
            continue

        for option_value, new_percent in option_map.items():
            rows = frappe.get_all(
                "Buyback Question Option",
                filters={"parent": ["in", questions], "option_value": option_value},
                fields=["name", "parent", "price_impact_percent"],
            )
            for row in rows:
                current = float(row.price_impact_percent or 0)
                if abs(current - new_percent) < 0.001:
                    continue
                frappe.db.set_value(
                    "Buyback Question Option", row.name,
                    "price_impact_percent", new_percent,
                    update_modified=False,
                )
                changed += 1
                print(
                    f"  {question_code}.{option_value}: {current}% → {new_percent}% "
                    f"({REASONS.get(question_code, '')})"
                )

    frappe.db.commit()
    print(f"✔ Corrected {changed} question option impacts")
