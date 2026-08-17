"""Group questions that ask about the same physical fault under one fault code.

Every item is mapped to 35 customer questions plus 19 automated tests, and a
large share of those describe the same defect twice — the automated "Speaker"
test and the customer question "Speaker Faulty", "Microphone" and "Microphone
Not Working", and so on. The pricing engine summed both lists with no
de-duplication, so answering honestly charged the customer twice for one fault.
Summing the worst option of every mapped question reached 646% of the base
price.

Assigning a shared `fault_code` makes the engine charge each fault once, taking
the larger of the two configured impacts (see `_collect_deduction`).

Only unambiguous pairs are grouped. The generic "Camera Test" is deliberately
left alone rather than folded into the back/front camera faults, because
suppressing it would hide a real defect on a device where only one camera is
tested. Cosmetic ladders (screen/panel condition) are also untouched: they
become grade drivers rather than deductions, which is a separate change.

Idempotent — re-running only rewrites codes that differ.
"""

import frappe

# fault_code → question_codes that describe that one fault
FAULT_GROUPS = {
    "FAULT-SPEAKER":        ["speaker", "speaker_2"],
    "FAULT-MIC":            ["microphone", "mic"],
    "FAULT-BLUETOOTH":      ["bluetooth", "blutooth_not_work"],
    "FAULT-WIFI":           ["wifi", "wifi_not_working"],
    "FAULT-POWER-BTN":      ["power_button", "power"],
    "FAULT-VOLUME-BTN":     ["volume_buttons", "vlo"],
    "FAULT-PROXIMITY":      ["proximity_sensor", "pr"],
    "FAULT-VIBRATOR":       ["vibration", "vibara"],
    "FAULT-CAMERA-BACK":    ["ba", "back_camera"],
    "FAULT-CAMERA-FRONT":   ["fr", "front_camera"],
    "FAULT-CHARGING":       ["cha", "cahr"],
    "FAULT-RECEIVER":       ["ear_receiver", "audio_receiver_not_working", "ear_spea"],
    "FAULT-FINGERPRINT":    ["finger_print", "finger_touch_not_working"],
    "FAULT-FACE-ID":        ["tp", "face_sensor_not_working"],
    "FAULT-NETWORK":        ["network", "sim_network_problem"],
    "FAULT-TOUCHSCREEN":    ["multi_touch", "touch_screen_working_or_not"],
    "FAULT-BATTERY-DEFECT": ["battery", "battery_faulty"],
    # Three overlapping health thresholds (<80%, 80-85%, <85%) that could all
    # fire at once. Collapsing them to one fault is the interim fix; the spec
    # calls for a single "battery health below 85%" question.
    "FAULT-BATTERY-HEALTH": ["bat", "bat_2", "ba_2"],
    # Flip and fold hinge questions — the spec has one 15% row for all of them.
    "FAULT-HINGE":          ["if_flip_mobile_hinges_opening_properly",
                             "if_fold_mobile", "is_hinge_working_properly"],
}


def execute():
    if not frappe.db.table_exists("Buyback Question Bank"):
        return
    if not frappe.db.has_column("Buyback Question Bank", "fault_code"):
        frappe.logger("patch").warning(
            "v0_0_21_group_duplicate_question_faults: fault_code column not synced "
            "yet — skipping (will run after the next bench migrate)"
        )
        return

    updated = 0
    missing = []

    for fault_code, question_codes in FAULT_GROUPS.items():
        for question_code in question_codes:
            rows = frappe.get_all(
                "Buyback Question Bank",
                filters={"question_code": question_code},
                fields=["name", "fault_code"],
            )
            if not rows:
                missing.append(question_code)
                continue
            for row in rows:
                if (row.fault_code or "") != fault_code:
                    frappe.db.set_value(
                        "Buyback Question Bank", row.name, "fault_code", fault_code,
                        update_modified=False,
                    )
                    updated += 1

    frappe.db.commit()

    print(f"✔ Grouped {updated} questions into {len(FAULT_GROUPS)} fault codes")
    if missing:
        # Not an error: sites seeded differently will not have every code.
        print(f"  ℹ {len(missing)} question codes not present on this site: "
              f"{', '.join(sorted(missing))}")
