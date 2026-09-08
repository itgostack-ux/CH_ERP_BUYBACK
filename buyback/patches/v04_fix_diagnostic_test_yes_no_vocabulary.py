"""Correct 17 misconfigured "Automated Test" diagnostic questions from
Pass/Fail to Yes/No.

``BuybackAssessment.diagnostic_tests`` documents its own design: "Automated
test results from mobile app — each test produces Yes/No." Of the 19
Automated Test entries in Buyback Question Bank, only "Charging" and
"Multi Touch" actually used Yes/No — the other 17 (Battery, Bluetooth,
Camera Test, Screen, ...) were configured with Pass/Fail, which no
diagnostic capture path ever produces. That made every self-reported "Yes"
for those 17 tests look like an inspector mismatch, when it was really the
question's own options that were wrong.

Renames the Buyback Question Option value+label only — price_impact_percent
is untouched, since Pass's 0% carries straight over to Yes's 0% and Fail's
deduction to No's (confirmed against "cha"/"multi_touch", which already
used the correct vocabulary with the same impact pattern). Then backfills
existing Buyback Assessment Diagnostic / Buyback Inspection Diagnostic rows
for these test codes so they stay valid against the corrected options.
"""

from __future__ import annotations

import frappe

AFFECTED_TEST_CODES = (
    "ba", "battery", "bluetooth", "camera_test", "ear_receiver",
    "finger_print", "flash_light", "fr", "gps", "microphone",
    "power_button", "proximity_sensor", "screen", "speaker",
    "vibration", "volume_buttons", "wifi",
)

RENAME = {"Pass": "Yes", "Fail": "No"}


def execute():
    if not frappe.db.table_exists("Buyback Question Bank"):
        return

    qbank_names = frappe.get_all(
        "Buyback Question Bank",
        filters={
            "diagnosis_type": "Automated Test",
            "question_code": ["in", AFFECTED_TEST_CODES],
        },
        pluck="name",
    )
    if not qbank_names:
        return

    for old, new in RENAME.items():
        frappe.db.sql(
            """
            UPDATE `tabBuyback Question Option`
            SET option_value = %(new)s, option_label = %(new)s
            WHERE parent IN %(parents)s AND option_value = %(old)s
            """,
            {"new": new, "old": old, "parents": tuple(qbank_names)},
        )

    for doctype, result_fields in (
        ("Buyback Assessment Diagnostic", ("result",)),
        ("Buyback Inspection Diagnostic", ("assessment_result", "inspector_result")),
    ):
        if not frappe.db.table_exists(doctype):
            continue
        for field in result_fields:
            for old, new in RENAME.items():
                frappe.db.sql(
                    f"""
                    UPDATE `tab{doctype}`
                    SET `{field}` = %(new)s
                    WHERE test_code IN %(codes)s AND `{field}` = %(old)s
                    """,
                    {"new": new, "old": old, "codes": AFFECTED_TEST_CODES},
                )
