"""The buyback question catalogue and the three device question sets.

Structure follows what Cashify and Cashkr actually do, which is a funnel rather
than a flat questionnaire:

  1. Eligibility  — activation locks and account removal. A device that fails
                    these cannot be resold at any grade, so they gate the deal
                    instead of shaving a percentage off it.
  2. Grading      — single-select condition ladders whose rungs escalate in
                    severity. Each rung declares the WORST grade it allows; the
                    device takes the lowest grade any answer forces. This is the
                    half that decides which Price Master cell is used.
  3. Deduction    — component faults, priced as a percentage of the graded
                    price.

Every question is defined once, here, and referenced by the sets that use it.
Questions shared across Apple, Android and foldables are one record, not three:
the old bank asked "Speaker" and "Speaker Faulty" as separate questions and
charged for both.

Device age is deliberately NOT a question. It already lives on the assessment
as `device_age_months` and selects the price band; asking it again would be
exactly the repetition this catalogue exists to remove. Warranty status is
derived from it for the same reason.

Percentages are the Android/default rates from the depreciation sheet. Faults
whose rate the sheet does not specify are seeded at 0 and listed by
`unrated_faults()` so a pricing owner can fill them; the Apple column arrives
with the brand-family split.
"""

# Option tuple: (value, label, forces_grade, price_impact_percent)
# forces_grade of None means the answer does not limit the grade.

GRADING = "Grading"
DEDUCTION = "Deduction"
ELIGIBILITY = "Eligibility"

QUESTIONS: dict[str, dict] = {
    # ── Eligibility ────────────────────────────────────────────────
    "elig_icloud_lock": {
        "text": "iCloud lock check",
        "purpose": ELIGIBILITY, "category": "Software", "fault_code": "FAULT-ICLOUD-LOCK",
        "options": [
            ("not_locked", "Not locked", None, 0),
            ("locked", "Locked", None, 0),
        ],
    },
    "elig_country_lock": {
        "text": "Is the device carrier or country locked?",
        "purpose": ELIGIBILITY, "category": "Software", "fault_code": "FAULT-COUNTRY-LOCK",
        "options": [
            ("no", "Not locked", None, 0),
            ("yes", "Carrier / country locked", None, 0),
        ],
    },
    "elig_account_removed": {
        "text": "Google, Xiaomi or Samsung account removed?",
        "purpose": ELIGIBILITY, "category": "Software", "fault_code": "FAULT-ACCOUNT-REMOVED",
        "options": [
            ("yes", "Removed", None, 0),
            ("no", "Not removed", None, 0),
        ],
    },

    # ── Screen — grading ladders ───────────────────────────────────
    "scr_display_working": {
        "text": "Is the touch screen and display working properly?",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-DISPLAY-DEAD",
        "options": [
            ("working", "Yes, working properly", None, 0),
            ("not_working", "No, not working", "D", 0),
        ],
    },
    "scr_is_copy": {
        "text": "Is the screen a copy or duplicate part?",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-DISPLAY-COPY",
        "options": [
            ("original", "Original screen", None, 0),
            ("copy", "Copy / duplicate screen", "D", 0),
        ],
    },
    "scr_spots": {
        "text": "Are there any visible spots on the screen?",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-SCREEN-SPOTS",
        "options": [
            ("none", "No spots", None, 0),
            ("upto_3_white", "Up to 3 white spots of 2mm, or 1 white spot of 3mm", "B", 0),
            ("over_3_white", "More than 3 white spots / white patches", "C", 0),
            ("coloured", "Coloured spots or patches (black, yellow, blue, green, red)", "D", 0),
        ],
    },
    "scr_lines": {
        "text": "Are there any visible lines on the screen?",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-SCREEN-LINES",
        "options": [
            ("none", "No lines on screen", None, 0),
            ("lines", "Lines on screen", "D", 0),
        ],
    },
    "scr_discolouration": {
        "text": "Is the screen discoloured?",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-SCREEN-DISCOLOUR",
        "options": [
            ("none", "No discolouration", None, 0),
            ("minor", "Minor — very slight shade along the edges, not clearly visible", "B", 0),
            ("major", "Major — yellow / blue / pink / green shade along the edges", "C", 0),
            ("fading", "Screen fading — colour or background imprint on screen", "C", 0),
        ],
    },
    "scr_cracks_scratches": {
        "text": "Is the screen cracked or scratched?",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-SCREEN-SCRATCH",
        "options": [
            ("none", "Excellent — no scratch visible", None, 0),
            ("upto_5_under_1cm", "Up to 5 scratches under 1cm", "B", 0),
            ("upto_10_1cm", "Up to 10 scratches of 1cm, or up to 5 of 1.1cm–2.5cm", "C", 0),
            ("over_10_1cm", "Over 10 scratches of 1cm, over 5 of 1.1cm–2.5cm, or 1 scratch over 2.5cm", "C", 0),
            ("chipped", "Screen chipped — minor chipping along the edges", "C", 0),
        ],
    },
    "scr_bubble_paint": {
        "text": "Is there paint peel-off or bubbling on the screen?",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-SCREEN-PAINT",
        "options": [
            ("none", "No paint peel-off or bubble", None, 0),
            ("minor", "Minor paint peel-off, or fewer than 2 bubbles", "B", 0),
            ("major", "Major paint peel-off, or more than 2 bubbles", "C", 0),
        ],
    },
    "scr_flickering": {
        "text": "Does the screen flicker?",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-SCREEN-FLICKER",
        "options": [
            ("none", "No flickering", None, 0),
            ("flickering", "Flickering on screen", "D", 0),
        ],
    },

    # ── Screen — foldable only ─────────────────────────────────────
    "scr_outer_display": {
        "text": "Outer screen condition",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-OUTER-SCREEN",
        "options": [
            ("ok", "No issue with the outer screen", None, 0),
            ("damaged", "Outer screen damaged — line, break or spot", "D", 0),
        ],
    },
    "scr_inner_display": {
        "text": "Inner screen condition",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-INNER-SCREEN",
        "options": [
            ("ok", "No issue with the inner screen", None, 0),
            ("damaged", "Inner screen damaged — line, break or spot", "D", 0),
        ],
    },

    # ── Body — grading ladders ─────────────────────────────────────
    "body_scratches": {
        "text": "Are there any scratches on the phone body?",
        "purpose": GRADING, "category": "Physical", "fault_code": "FAULT-BODY-SCRATCH",
        "options": [
            ("none", "Excellent — no scratches", None, 0),
            ("upto_5_under_1cm", "Up to 5 scratches under 1cm", "B", 0),
            ("upto_15_1cm", "Up to 15 scratches of 1cm, or up to 5 of 3cm", "C", 0),
            ("over_15_1cm", "Over 15 scratches of 1cm, over 5 of 3cm, or a scratch over 3cm", "C", 0),
            ("paint_bubble", "Minor paint peel-off or bubbling", "B", 0),
        ],
    },
    "body_dents": {
        "text": "Are there any dents on the phone body?",
        "purpose": GRADING, "category": "Physical", "fault_code": "FAULT-BODY-DENT",
        "options": [
            ("none", "No dents", None, 0),
            ("under_2", "Fewer than 2 dents, up to 2mm", "C", 0),
            ("over_2_or_crack", "More than 2 dents, or a crack under 1cm on the body", "C", 0),
        ],
    },
    "body_panel": {
        "text": "Panel physical condition",
        "purpose": GRADING, "category": "Physical", "fault_code": "FAULT-BODY-PANEL",
        "options": [
            ("none", "No defect", None, 0),
            ("loose", "Loose panel — visible gap over 0.25mm, or visible pasting", "C", 0),
            ("missing", "Missing panel", "D", 0),
            ("cracked", "Cracked or broken panel", "D", 0),
            ("glass_back", "Glass back panel damaged", "D", 0),
        ],
    },
    "body_bent": {
        "text": "Is the phone bent?",
        "purpose": GRADING, "category": "Physical", "fault_code": "FAULT-BODY-BENT",
        "options": [
            ("none", "Phone not bent", None, 0),
            ("loose_screen", "Loose screen — over 0.25mm gap, or visible pasting between body and screen", "C", 0),
            ("bent", "Bent frame or panel — curve visible on the screen surface", "D", 0),
        ],
    },

    # ── Functional — deductions ────────────────────────────────────
    "fn_volume_button": {
        "text": "Volume button",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-VOLUME-BTN",
        "options": [
            ("present", "Working", None, 0),
            ("missing", "Missing or not working", None, 2),
        ],
    },
    "fn_power_button": {
        "text": "Power button",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-POWER-BTN",
        "options": [
            ("present", "Working", None, 0),
            ("missing", "Missing or not working", None, 2),
        ],
    },
    "fn_sim_tray": {
        "text": "SIM tray",
        "purpose": DEDUCTION, "category": "Physical", "fault_code": "FAULT-SIM-TRAY",
        "options": [
            ("available", "Available and intact", None, 0),
            ("broken", "Broken or missing", None, 0),  # rate not on the sheet
        ],
    },
    "fn_ear_speaker": {
        "text": "Ear speaker / receiver",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-RECEIVER",
        "options": [
            ("working", "Working", None, 0),
            ("not_working", "Not working or low", None, 3),
        ],
    },
    "fn_gps": {
        "text": "GPS",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-GPS",
        "options": [
            ("working", "Working", None, 0),
            ("not_working", "Not working", None, 2),
        ],
    },
    "fn_face_id": {
        "text": "Face ID unlock test",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-FACE-ID",
        "options": [
            ("working", "Face ID is working", None, 0),
            ("not_working", "Face ID is not working", None, 25),
            ("not_present", "Face ID not present on this model", None, 0),
        ],
    },
    "fn_finger_touch": {
        "text": "Fingerprint / touch unlock test",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-FINGERPRINT",
        "options": [
            ("working", "Working", None, 0),
            ("not_working", "Not working", None, 5),
            ("not_present", "Not present on this model", None, 0),
        ],
    },
    "fn_hinge": {
        "text": "Do the hinges open and fold properly?",
        "purpose": DEDUCTION, "category": "Physical", "fault_code": "FAULT-HINGE",
        "options": [
            ("working", "Yes, opens and folds properly", None, 0),
            ("not_working", "No, does not open or fold properly", None, 15),
        ],
    },

    # ── SIM / network — deductions ─────────────────────────────────
    "sim_1_working": {
        "text": "Is SIM slot 1 working?",
        "purpose": DEDUCTION, "category": "Network", "fault_code": "FAULT-SIM-1",
        "options": [
            ("yes", "Working", None, 0),
            ("no", "Not working", None, 0),  # rate not on the sheet
        ],
    },
    "sim_2_working": {
        "text": "Is SIM slot 2 working?",
        "purpose": DEDUCTION, "category": "Network", "fault_code": "FAULT-SIM-2",
        "options": [
            ("yes", "Working", None, 0),
            ("no", "Not working", None, 0),  # rate not on the sheet
            ("not_present", "Single-SIM device", None, 0),
        ],
    },
    "sim_calls": {
        "text": "Can the device make and receive calls?",
        "purpose": GRADING, "category": "Network", "fault_code": "FAULT-CALLS",
        "options": [
            ("yes", "Yes", None, 0),
            ("no", "No", "D", 0),
        ],
    },
    "sim_esim_support": {
        "text": "How many eSIMs does the device support?",
        "purpose": DEDUCTION, "category": "Network", "fault_code": "FAULT-ESIM-COUNT",
        "options": [
            ("single_esim", "Single eSIM", None, 0),
            ("dual_esim", "Dual eSIM", None, 0),
            ("both_physical", "Both physical SIM", None, 0),
        ],
    },

    # ── Camera — deductions ────────────────────────────────────────
    "cam_front": {
        "text": "Is the front camera image blurred, spotted or distorted?",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-CAMERA-FRONT",
        "options": [
            ("no_issue", "No issues", None, 0),
            ("issue", "Blurred, spotted or distorted", None, 5),
        ],
    },
    "cam_back": {
        "text": "Is the back camera image blurred, spotted or distorted?",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-CAMERA-BACK",
        "options": [
            ("no_issue", "No issues", None, 0),
            ("issue", "Blurred, spotted or distorted", None, 5),
        ],
    },
    "cam_glass": {
        "text": "Is the camera glass broken?",
        "purpose": DEDUCTION, "category": "Physical", "fault_code": "FAULT-CAMERA-GLASS",
        "options": [
            ("no", "Intact", None, 0),
            ("yes", "Broken", None, 3),
        ],
    },

    # ── Apple unknown parts — deductions ───────────────────────────
    "apl_unknown_display": {
        "text": "Is the display part reported as unknown?",
        "purpose": DEDUCTION, "category": "Diagnosis", "fault_code": "FAULT-UNKNOWN-DISPLAY",
        "options": [
            ("known", "No, the display part is genuine", None, 0),
            ("unknown", "Yes, the display part is unknown", None, 0),  # rate not on the sheet
        ],
    },
    "apl_unknown_camera": {
        "text": "Is the camera part reported as unknown?",
        "purpose": DEDUCTION, "category": "Diagnosis", "fault_code": "FAULT-UNKNOWN-CAMERA",
        "options": [
            ("known", "No, the camera part is genuine", None, 0),
            ("unknown", "Yes, the camera part is unknown", None, 3),
        ],
    },
    "apl_unknown_battery": {
        "text": "Is the battery part reported as unknown?",
        "purpose": DEDUCTION, "category": "Diagnosis", "fault_code": "FAULT-UNKNOWN-BATTERY",
        "options": [
            ("known", "No, the battery part is genuine", None, 0),
            ("unknown", "Yes, the battery part is unknown", None, 4),
        ],
    },

    # ── Battery — deductions, one question per platform ────────────
    "bat_health_ios": {
        "text": "Battery health",
        "purpose": DEDUCTION, "category": "Battery", "fault_code": "FAULT-BATTERY",
        "options": [
            ("above_85", "Above 85% — good", None, 0),
            ("80_to_85", "80–85% — moderate", None, 4),
            ("below_80", "Below 80% — service required or swollen", None, 7),
        ],
    },
    "bat_condition_android": {
        "text": "Battery condition",
        "purpose": DEDUCTION, "category": "Battery", "fault_code": "FAULT-BATTERY",
        "options": [
            ("healthy", "Healthy", None, 0),
            ("bulged", "Bulged or not working", None, 5),
        ],
    },

    # ── Commercial — deductions ────────────────────────────────────
    "com_accessories": {
        "text": "Which accessories are available?",
        "purpose": DEDUCTION, "category": "Accessories", "fault_code": "FAULT-ACCESSORIES",
        "options": [
            ("charger_and_box", "Charger and box", None, 0),
            ("charger_only", "Only charger", None, 5),
            ("box_only", "Only box", None, 5),
            ("neither", "Neither", None, 10),
        ],
    },
    "com_purchased_in_india": {
        "text": "Was the device purchased in India?",
        "purpose": DEDUCTION, "category": "General", "fault_code": "FAULT-PURCHASED-ABROAD",
        "options": [
            ("india", "Purchased in India", None, 0),
            ("abroad", "Not purchased in India", None, 8),
        ],
    },
}


# ── Sets ───────────────────────────────────────────────────────────
# Order is the order an inspector answers them: eligibility, then the
# grading ladders top-to-bottom, then the component deductions.

_SCREEN_CORE = [
    "scr_display_working", "scr_is_copy", "scr_spots", "scr_lines",
    "scr_discolouration", "scr_cracks_scratches", "scr_bubble_paint", "scr_flickering",
]
_BODY_CORE = ["body_scratches", "body_dents", "body_panel", "body_bent"]
_FUNCTIONAL_CORE = [
    "fn_volume_button", "fn_power_button", "fn_sim_tray",
    "fn_ear_speaker", "fn_gps", "fn_finger_touch",
]
_CAMERA_CORE = ["cam_front", "cam_back", "cam_glass"]
_SIM_CORE = ["sim_1_working", "sim_2_working", "sim_calls", "sim_esim_support"]
_COMMERCIAL = ["com_accessories", "com_purchased_in_india"]

SETS = [
    {
        "set_name": "Apple Handset",
        "brand_family": "Apple",
        "form_factor": "Standard",
        "description": (
            "iPhone and iPad. Adds Face ID, the three Apple unknown-part checks, "
            "battery health tiers, and the iCloud / carrier lock gates."
        ),
        "questions": (
            ["elig_icloud_lock", "elig_country_lock"]
            + _SIM_CORE
            + _SCREEN_CORE
            + _BODY_CORE
            + _FUNCTIONAL_CORE + ["fn_face_id"]
            + _CAMERA_CORE
            + ["apl_unknown_display", "apl_unknown_camera", "apl_unknown_battery"]
            + ["bat_health_ios"]
            + _COMMERCIAL
        ),
    },
    {
        "set_name": "Android Handset",
        "brand_family": "Android",
        "form_factor": "Standard",
        "description": (
            "Standard Android handsets. Battery is a bulged/healthy check rather "
            "than a health percentage, and the account-removal gate replaces the "
            "iCloud lock."
        ),
        "questions": (
            ["elig_account_removed"]
            + _SIM_CORE
            + _SCREEN_CORE
            + _BODY_CORE
            + _FUNCTIONAL_CORE
            + _CAMERA_CORE
            + ["bat_condition_android"]
            + _COMMERCIAL
        ),
    },
    {
        "set_name": "Foldable Handset",
        "brand_family": "Android",
        "form_factor": "Foldable",
        "description": (
            "Book-fold and clamshell handsets. Everything in the Android set plus "
            "the inner screen, outer screen and hinge checks."
        ),
        "questions": (
            ["elig_account_removed"]
            + _SIM_CORE
            + _SCREEN_CORE + ["scr_outer_display", "scr_inner_display"]
            + _BODY_CORE + ["fn_hinge"]
            + _FUNCTIONAL_CORE
            + _CAMERA_CORE
            + ["bat_condition_android"]
            + _COMMERCIAL
        ),
    },
]


def unrated_faults() -> list[str]:
    """Questions whose worst answer still deducts nothing.

    These are the faults the depreciation sheet does not price. They are seeded
    at 0 rather than guessed at, and listed here so a pricing owner can see
    exactly what is outstanding.
    """
    out = []
    for code, q in QUESTIONS.items():
        if q["purpose"] != DEDUCTION:
            continue
        if not any(opt[3] for opt in q["options"]):
            out.append(code)
    return sorted(out)


def validate_catalogue() -> None:
    """Fail loudly on a catalogue that would reintroduce double-charging.

    Sharing a fault code across the catalogue is legitimate — `bat_health_ios`
    and `bat_condition_android` are the same fault asked in the vocabulary of
    each platform, and never appear together. What must never happen is one
    SET carrying two questions for one fault, which is what charged customers
    twice before. That is the invariant checked here, and again in
    BuybackQuestionSet.validate() so a hand-edited set cannot drift.
    """
    for spec in SETS:
        codes = spec["questions"]
        if len(codes) != len(set(codes)):
            dupes = {c for c in codes if codes.count(c) > 1}
            raise ValueError(f"{spec['set_name']} repeats: {sorted(dupes)}")

        faults_in_set: dict[str, str] = {}
        for code in codes:
            if code not in QUESTIONS:
                raise ValueError(f"{spec['set_name']} references unknown question {code}")
            fault = QUESTIONS[code].get("fault_code")
            if fault and fault in faults_in_set:
                raise ValueError(
                    f"{spec['set_name']} covers fault {fault} twice: "
                    f"{faults_in_set[fault]} and {code}"
                )
            faults_in_set[fault] = code
