"""
Seed the Question Bank with 10 automated diagnostic tests.

Each test has three options: Pass (0%), Partial (−X%), Fail (−Y%).
Run:  bench --site erpnext.local execute buyback.setup.seed_diagnostic_tests.run
"""

import frappe


TESTS = [
    {
        "question_text": "Screen Test",
        "question_code": "screen_test",
        "display_order": 1,
        "options": [
            {"option_value": "Pass",    "option_label": "Pass",    "price_impact_percent": 0},
            {"option_value": "Partial", "option_label": "Partial", "price_impact_percent": -10},
            {"option_value": "Fail",    "option_label": "Fail",    "price_impact_percent": -25},
        ],
    },
    {
        "question_text": "Touch Test",
        "question_code": "touch_test",
        "display_order": 2,
        "options": [
            {"option_value": "Pass",    "option_label": "Pass",    "price_impact_percent": 0},
            {"option_value": "Partial", "option_label": "Partial", "price_impact_percent": -8},
            {"option_value": "Fail",    "option_label": "Fail",    "price_impact_percent": -20},
        ],
    },
    {
        "question_text": "Speaker Test",
        "question_code": "speaker_test",
        "display_order": 3,
        "options": [
            {"option_value": "Pass",    "option_label": "Pass",    "price_impact_percent": 0},
            {"option_value": "Partial", "option_label": "Partial", "price_impact_percent": -3},
            {"option_value": "Fail",    "option_label": "Fail",    "price_impact_percent": -8},
        ],
    },
    {
        "question_text": "Microphone Test",
        "question_code": "mic_test",
        "display_order": 4,
        "options": [
            {"option_value": "Pass",    "option_label": "Pass",    "price_impact_percent": 0},
            {"option_value": "Partial", "option_label": "Partial", "price_impact_percent": -3},
            {"option_value": "Fail",    "option_label": "Fail",    "price_impact_percent": -8},
        ],
    },
    {
        "question_text": "Camera Test",
        "question_code": "camera_test",
        "display_order": 5,
        "options": [
            {"option_value": "Pass",    "option_label": "Pass",    "price_impact_percent": 0},
            {"option_value": "Partial", "option_label": "Partial", "price_impact_percent": -5},
            {"option_value": "Fail",    "option_label": "Fail",    "price_impact_percent": -15},
        ],
    },
    {
        "question_text": "Wi-Fi Test",
        "question_code": "wifi_test",
        "display_order": 6,
        "options": [
            {"option_value": "Pass",    "option_label": "Pass",    "price_impact_percent": 0},
            {"option_value": "Partial", "option_label": "Partial", "price_impact_percent": -3},
            {"option_value": "Fail",    "option_label": "Fail",    "price_impact_percent": -10},
        ],
    },
    {
        "question_text": "Bluetooth Test",
        "question_code": "bluetooth_test",
        "display_order": 7,
        "options": [
            {"option_value": "Pass",    "option_label": "Pass",    "price_impact_percent": 0},
            {"option_value": "Partial", "option_label": "Partial", "price_impact_percent": -2},
            {"option_value": "Fail",    "option_label": "Fail",    "price_impact_percent": -8},
        ],
    },
    {
        "question_text": "Accelerometer Test",
        "question_code": "accelerometer_test",
        "display_order": 8,
        "options": [
            {"option_value": "Pass",    "option_label": "Pass",    "price_impact_percent": 0},
            {"option_value": "Partial", "option_label": "Partial", "price_impact_percent": -2},
            {"option_value": "Fail",    "option_label": "Fail",    "price_impact_percent": -5},
        ],
    },
    {
        "question_text": "Battery Test",
        "question_code": "battery_test",
        "display_order": 9,
        "options": [
            {"option_value": "Pass",    "option_label": "Pass",    "price_impact_percent": 0},
            {"option_value": "Partial", "option_label": "Partial", "price_impact_percent": -5},
            {"option_value": "Fail",    "option_label": "Fail",    "price_impact_percent": -15},
        ],
    },
    {
        "question_text": "Fingerprint Test",
        "question_code": "fingerprint_test",
        "display_order": 10,
        "options": [
            {"option_value": "Pass",    "option_label": "Pass",    "price_impact_percent": 0},
            {"option_value": "Partial", "option_label": "Partial", "price_impact_percent": -3},
            {"option_value": "Fail",    "option_label": "Fail",    "price_impact_percent": -10},
        ],
    },
]


def run():
    created = 0
    skipped = 0

    for t in TESTS:
        if frappe.db.exists("Buyback Question Bank", {"question_code": t["question_code"]}):
            skipped += 1
            continue

        doc = frappe.new_doc("Buyback Question Bank")
        doc.question_text = t["question_text"]
        doc.question_code = t["question_code"]
        doc.diagnosis_type = "Automated Test"
        doc.question_type = "Single Select"
        doc.display_order = t["display_order"]
        doc.is_mandatory = 1

        for opt in t["options"]:
            doc.append("options", {
                "option_value": opt["option_value"],
                "option_label": opt["option_label"],
                "price_impact_percent": opt["price_impact_percent"],
            })

        doc.insert(ignore_permissions=True)
        created += 1

    # Also tag existing customer questions with diagnosis_type = "Customer Question"
    updated = frappe.db.sql(
        """UPDATE `tabBuyback Question Bank`
           SET diagnosis_type = 'Customer Question'
           WHERE diagnosis_type IS NULL OR diagnosis_type = ''"""
    )
    tagged = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabBuyback Question Bank` WHERE diagnosis_type = 'Customer Question'"
    )[0][0]

    frappe.db.commit()
    print(f"✔ Created {created} automated diagnostic tests, skipped {skipped} (already exist)")
    print(f"✔ Tagged {tagged} existing questions as 'Customer Question'")
