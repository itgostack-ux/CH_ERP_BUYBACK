"""Seed the question catalogue and the three device question sets.

Idempotent. Re-running rewrites question text, purpose, fault code and options
to match `question_catalogue`, so the catalogue module is the source of truth
and hand-edits in Desk are overwritten on the next migrate. Set membership is
rewritten the same way.

Run:  bench --site <site> execute buyback.setup.seed_question_sets.run
"""

import frappe

from buyback.setup.question_catalogue import (
    BRAND_FAMILY_ONLY,
    QUESTIONS,
    RETIRED_CODES,
    SETS,
    unrated_faults,
    validate_catalogue,
)


def _upsert_question(code: str, spec: dict) -> str:
    name = frappe.db.get_value("Buyback Question Bank", {"question_code": code}, "name")
    doc = (frappe.get_doc("Buyback Question Bank", name) if name
           else frappe.new_doc("Buyback Question Bank"))

    doc.question_code = code
    doc.question_text = spec["text"]
    doc.question_purpose = spec["purpose"]
    doc.fault_code = spec.get("fault_code")
    doc.question_type = "Single Select"
    # Everything lands in one list on the assessment. Splitting the same
    # device across an "Automated Test" table and a "Customer Question" table
    # is what let one fault be asked — and charged — twice.
    doc.diagnosis_type = "Customer Question"
    doc.disabled = 0
    doc.is_mandatory = 1

    if spec.get("category") and frappe.db.exists("Buyback Question Category", spec["category"]):
        doc.question_category = spec["category"]

    doc.applies_to_brand_family = BRAND_FAMILY_ONLY.get(code, "Any")

    doc.set("options", [])
    for value, label, forces_grade, percent, percent_apple in spec["options"]:
        doc.append("options", {
            "option_value": value,
            "option_label": label,
            "forces_grade": forces_grade or "",
            # Stored as a positive magnitude; the engine takes abs() either way.
            "price_impact_percent": percent,
            # Always written, even when both platforms pay the same. A Percent
            # field cannot hold NULL, so leaving it out would store 0 — which
            # reads as "free on Apple" rather than "same as Android".
            "price_impact_percent_apple": (
                percent if percent_apple is None else percent_apple),
        })

    doc.flags.ignore_permissions = True
    doc.save(ignore_permissions=True)
    return doc.name


def _upsert_set(spec: dict, question_names: dict[str, str]) -> str:
    name = frappe.db.get_value(
        "Buyback Question Set", {"set_name": spec["set_name"]}, "name"
    )
    doc = (frappe.get_doc("Buyback Question Set", name) if name
           else frappe.new_doc("Buyback Question Set"))

    doc.set_name = spec["set_name"]
    doc.applies_to_brand_family = spec["brand_family"]
    doc.applies_to_form_factor = spec["form_factor"]
    doc.description = spec["description"]
    doc.disabled = 0

    doc.set("questions", [])
    for position, code in enumerate(spec["questions"], start=1):
        doc.append("questions", {
            "question": question_names[code],
            "display_order": position * 10,
        })

    doc.save(ignore_permissions=True)
    return doc.name


# Reporting Category of the questions that are not in the catalogue above: the
# older customer questions and the app's automated diagnostic tests. Taken
# from the category sheet the business keeps for the question bank — that
# sheet decides, so a category changed in Desk for one of these codes is put
# back on the next migrate; change it here instead.
LEGACY_QUESTION_CATEGORIES = {
    "Accessories": (
        "bill_with_same_imei", "original_box_with_same_imei", "access",
    ),
    "General": (
        "if_fold_mobile", "if_flip_mobile_hinges_opening_properly", "manufacturer_warrenty",
        "is_your_phones_screen_original", "touch_screen_working_or_not",
        "is_the_phone_in_proper_working_condition",
    ),
    "Physical": (
        "is_hinge_working_properly", "is_crease_normal", "is_cover_screen_working",
        "back_panel_condition", "center_or_side_panel_condition", "touch_glass_condition",
        "screen_condition",
    ),
    "Functional": (
        "bat_2", "ba_2", "audio_receiver_not_working", "silent_button_not_working",
        "face_sensor_not_working", "battery_faulty", "wifi_not_working",
        "finger_touch_not_working", "sim_network_problem", "bat", "vibara", "ear_spea",
        "power", "pr", "cahr", "blutooth_not_work", "vlo", "tp", "mic", "speaker_2",
        "network", "camera_glass_broken", "back_camera", "front_camera",
    ),
    # Every automated test: run by the device, not asked of the customer.
    "Diagnosis": (
        "cha", "multi_touch", "ba", "fr", "volume_buttons", "vibration", "speaker",
        "screen", "proximity_sensor", "wifi", "power_button", "microphone", "gps",
        "flash_light", "finger_print", "ear_receiver", "camera_test", "bluetooth", "battery",
    ),
}
# An automated test added later, and not yet on the sheet, is reported with
# the rest of them until somebody says otherwise.
AUTOMATED_TEST_CATEGORY = "Diagnosis"


# Older customer questions that ask again what the device is already tested
# for, or what a later question asks: the app's automated tests cover Wi-Fi,
# battery, fingerprint, proximity and the ear receiver, and "Network Problem",
# "Touch ID Or Face ID Working" and "Ear Speaker not Working Or Low" replaced
# the rest. Shown alongside those they made the customer answer the same thing
# twice — and could deduct for one fault twice — so they are switched off.
# Disabled, not deleted: answers already given on past assessments keep them.
REPEATED_QUESTION_CODES = (
    "sim_network_problem",          # asked again as "Network Problem"
    "finger_touch_not_working",     # Finger Print test / "Touch ID Or Face ID Working"
    "wifi_not_working",             # Wi-Fi test
    "battery_faulty",               # Battery test
    "face_sensor_not_working",      # Proximity Sensor test / "Touch ID Or Face ID Working"
    "silent_button_not_working",
    "audio_receiver_not_working",   # Ear Receiver test / "Ear Speaker not Working Or Low"
)


def _retire_repeated_questions() -> int:
    names = frappe.get_all(
        "Buyback Question Bank",
        filters={"question_code": ("in", REPEATED_QUESTION_CODES), "disabled": 0},
        pluck="name")
    for name in names:
        frappe.db.set_value("Buyback Question Bank", name, "disabled", 1, update_modified=False)
    return len(names)


def _ensure_category(category: str) -> None:
    if not frappe.db.exists("Buyback Question Category", category):
        frappe.get_doc({
            "doctype": "Buyback Question Category", "category_name": category,
        }).insert(ignore_permissions=True)


def _categorise_legacy_questions() -> int:
    """Set the Reporting Category of the questions the catalogue does not own.

    Catalogue questions are skipped — the catalogue sets theirs — so the two
    never write the same row.
    """
    changed = 0
    for category, codes in LEGACY_QUESTION_CATEGORIES.items():
        _ensure_category(category)
        for code in codes:
            if code in QUESTIONS:
                continue
            for name, current in frappe.get_all(
                    "Buyback Question Bank", filters={"question_code": code},
                    fields=["name", "question_category"], as_list=True):
                if current != category:
                    frappe.db.set_value(
                        "Buyback Question Bank", name, "question_category", category,
                        update_modified=False)
                    changed += 1
    _ensure_category(AUTOMATED_TEST_CATEGORY)
    for name in frappe.get_all(
            "Buyback Question Bank",
            filters={"diagnosis_type": "Automated Test", "question_category": ("in", ("", None))},
            pluck="name"):
        frappe.db.set_value(
            "Buyback Question Bank", name, "question_category", AUTOMATED_TEST_CATEGORY,
            update_modified=False)
        changed += 1
    return changed


def run(retire_legacy: int = 0):
    """Seed catalogue + sets.

    Args:
        retire_legacy: when truthy, disable every Question Bank row that the
            catalogue does not define. Off by default — retiring the old bank
            changes what inspectors are asked, so it is an explicit decision
            rather than a side effect of a migrate.
    """
    validate_catalogue()

    question_names = {code: _upsert_question(code, spec) for code, spec in QUESTIONS.items()}
    print(f"✔ {len(question_names)} questions upserted")

    for spec in SETS:
        set_name = _upsert_set(spec, question_names)
        print(f"✔ {set_name}: {len(spec['questions'])} questions")

    repeated = _retire_repeated_questions()
    if repeated:
        print(f"✔ {repeated} repeated customer question(s) switched off")

    categorised = _categorise_legacy_questions()
    if categorised:
        print(f"✔ Reporting Category set on {categorised} question(s) outside the catalogue")

    # Codes this catalogue owned and dropped. Always disabled, regardless of
    # retire_legacy: leaving one enabled but in no set means it can still be
    # answered through the API and still deduct.
    for code in RETIRED_CODES:
        name = frappe.db.get_value(
            "Buyback Question Bank", {"question_code": code, "disabled": 0}, "name")
        if name:
            frappe.db.set_value(
                "Buyback Question Bank", name, "disabled", 1, update_modified=False)
            print(f"✔ retired superseded question {code}")

    retired = 0
    if int(retire_legacy or 0):
        keep = set(QUESTIONS)
        for row in frappe.get_all(
            "Buyback Question Bank", filters={"disabled": 0}, fields=["name", "question_code"]
        ):
            if row.question_code not in keep:
                frappe.db.set_value(
                    "Buyback Question Bank", row.name, "disabled", 1, update_modified=False
                )
                retired += 1
        print(f"✔ {retired} legacy questions disabled")

    outstanding = unrated_faults()
    if outstanding:
        print(
            f"  ! {len(outstanding)} faults still deduct nothing — the depreciation "
            f"sheet does not price them: {', '.join(outstanding)}"
        )

    frappe.db.commit()
    return {
        "questions": len(question_names),
        "sets": len(SETS),
        "retired": retired,
        "unrated": outstanding,
    }
