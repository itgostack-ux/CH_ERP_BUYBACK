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

Rates carry both platforms: the depreciation sheet prices roughly twenty faults
differently on Apple. Faults the sheet does not price at all are seeded at 0 and
listed by `unrated_faults()` so a pricing owner can see exactly what is
outstanding, rather than a guess quietly becoming policy.
"""

# Option tuple: (value, label, forces_grade, percent, percent_apple)
#
#   forces_grade   None means the answer does not limit the grade.
#   percent        deduction on Android, and on Apple when percent_apple is None.
#   percent_apple  Apple rate where the depreciation sheet differs. None means
#                  "same as Android" — NOT "free on Apple".
#
# Rates come from the depreciation sheet. Where it gives only one figure the
# Apple column stays None; where it prices a fault on one platform only, the
# question is confined to that platform's set rather than zero-rated on the
# other, so a rate here is never silently unreachable.

GRADING = "Grading"
DEDUCTION = "Deduction"
ELIGIBILITY = "Eligibility"

QUESTIONS: dict[str, dict] = {
    # ── Eligibility ────────────────────────────────────────────────
    "elig_icloud_lock": {
        "text": "iCloud lock check",
        "purpose": ELIGIBILITY, "category": "Software", "fault_code": "FAULT-ICLOUD-LOCK",
        "options": [
            ("not_locked", "Not locked", None, 0, None),
            ("locked", "Locked", None, 0, None),
        ],
    },
    "elig_country_lock": {
        "text": "Is the device carrier or country locked?",
        "purpose": ELIGIBILITY, "category": "Software", "fault_code": "FAULT-COUNTRY-LOCK",
        "options": [
            ("no", "Not locked", None, 0, None),
            ("yes", "Carrier / country locked", None, 0, None),
        ],
    },
    "elig_account_removed": {
        "text": "Google, Xiaomi or Samsung account removed?",
        "purpose": ELIGIBILITY, "category": "Software", "fault_code": "FAULT-ACCOUNT-REMOVED",
        "options": [
            ("yes", "Removed", None, 0, None),
            ("no", "Not removed", None, 0, None),
        ],
    },

    # ── Screen — grading ladders ───────────────────────────────────
    "scr_display_working": {
        "text": "Is the touch screen and display working properly?",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-DISPLAY-DEAD",
        "options": [
            ("working", "Yes, working properly", None, 0, None),
            ("not_working", "No, not working", "D", 0, None),
        ],
    },
    "scr_is_copy": {
        "text": "Is the screen a copy or duplicate part?",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-DISPLAY-COPY",
        "options": [
            ("original", "Original screen", None, 0, None),
            ("copy", "Copy / duplicate screen", "D", 0, None),
        ],
    },
    "scr_spots": {
        "text": "Are there any visible spots on the screen?",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-SCREEN-SPOTS",
        "options": [
            ("none", "No spots", None, 0, None),
            ("upto_3_white", "Up to 3 white spots of 2mm, or 1 white spot of 3mm", "B", 0, None),
            ("over_3_white", "More than 3 white spots / white patches", "C", 0, None),
            ("coloured", "Coloured spots or patches (black, yellow, blue, green, red)", "D", 0, None),
        ],
    },
    "scr_lines": {
        "text": "Are there any visible lines on the screen?",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-SCREEN-LINES",
        "options": [
            ("none", "No lines on screen", None, 0, None),
            ("lines", "Lines on screen", "D", 0, None),
        ],
    },
    "scr_discolouration": {
        "text": "Is the screen discoloured?",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-SCREEN-DISCOLOUR",
        "options": [
            ("none", "No discolouration", None, 0, None),
            ("minor", "Minor — very slight shade along the edges, not clearly visible", "B", 0, None),
            ("major", "Major — yellow / blue / pink / green shade along the edges", "C", 0, None),
            ("fading", "Screen fading — colour or background imprint on screen", "C", 0, None),
        ],
    },
    "scr_cracks_scratches": {
        "text": "Is the screen cracked or scratched?",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-SCREEN-SCRATCH",
        "options": [
            ("none", "Excellent — no scratch visible", None, 0, None),
            ("upto_5_under_1cm", "Up to 5 scratches under 1cm", "B", 0, None),
            ("upto_10_1cm", "Up to 10 scratches of 1cm, or up to 5 of 1.1cm–2.5cm", "C", 0, None),
            ("over_10_1cm", "Over 10 scratches of 1cm, over 5 of 1.1cm–2.5cm, or 1 scratch over 2.5cm", "C", 0, None),
            ("chipped", "Screen chipped — minor chipping along the edges", "C", 0, None),
        ],
    },
    "scr_bubble_paint": {
        "text": "Is there paint peel-off or bubbling on the screen?",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-SCREEN-PAINT",
        "options": [
            ("none", "No paint peel-off or bubble", None, 0, None),
            ("minor", "Minor paint peel-off, or fewer than 2 bubbles", "B", 0, None),
            ("major", "Major paint peel-off, or more than 2 bubbles", "C", 0, None),
        ],
    },
    "scr_flickering": {
        "text": "Does the screen flicker?",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-SCREEN-FLICKER",
        "options": [
            ("none", "No flickering", None, 0, None),
            ("flickering", "Flickering on screen", "D", 0, None),
        ],
    },

    # ── Screen — foldable only ─────────────────────────────────────
    "scr_outer_display": {
        "text": "Outer screen condition",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-OUTER-SCREEN",
        "options": [
            ("ok", "No issue with the outer screen", None, 0, None),
            ("damaged", "Outer screen damaged — line, break or spot", "D", 0, None),
        ],
    },
    "scr_inner_display": {
        "text": "Inner screen condition",
        "purpose": GRADING, "category": "Cosmetic", "fault_code": "FAULT-INNER-SCREEN",
        "options": [
            ("ok", "No issue with the inner screen", None, 0, None),
            ("damaged", "Inner screen damaged — line, break or spot", "D", 0, None),
        ],
    },

    # ── Body — grading ladders ─────────────────────────────────────
    "body_scratches": {
        "text": "Are there any scratches on the phone body?",
        "purpose": GRADING, "category": "Physical", "fault_code": "FAULT-BODY-SCRATCH",
        "options": [
            ("none", "Excellent — no scratches", None, 0, None),
            ("upto_5_under_1cm", "Up to 5 scratches under 1cm", "B", 0, None),
            ("upto_15_1cm", "Up to 15 scratches of 1cm, or up to 5 of 3cm", "C", 0, None),
            ("over_15_1cm", "Over 15 scratches of 1cm, over 5 of 3cm, or a scratch over 3cm", "C", 0, None),
            ("paint_bubble", "Minor paint peel-off or bubbling", "B", 0, None),
        ],
    },
    "body_dents": {
        "text": "Are there any dents on the phone body?",
        "purpose": GRADING, "category": "Physical", "fault_code": "FAULT-BODY-DENT",
        "options": [
            ("none", "No dents", None, 0, None),
            ("under_2", "Fewer than 2 dents, up to 2mm", "C", 0, None),
            ("over_2_or_crack", "More than 2 dents, or a crack under 1cm on the body", "C", 0, None),
        ],
    },
    "body_panel": {
        "text": "Panel physical condition",
        "purpose": GRADING, "category": "Physical", "fault_code": "FAULT-BODY-PANEL",
        "options": [
            ("none", "No defect", None, 0, None),
            ("loose", "Loose panel — visible gap over 0.25mm, or visible pasting", "C", 0, None),
            ("missing", "Missing panel", "D", 0, None),
            ("cracked", "Cracked or broken panel", "D", 0, None),
            ("glass_back", "Glass back panel damaged", "D", 0, None),
        ],
    },
    "body_bent": {
        "text": "Is the phone bent?",
        "purpose": GRADING, "category": "Physical", "fault_code": "FAULT-BODY-BENT",
        "options": [
            ("none", "Phone not bent", None, 0, None),
            ("loose_screen", "Loose screen — over 0.25mm gap, or visible pasting between body and screen", "C", 0, None),
            ("bent", "Bent frame or panel — curve visible on the screen surface", "D", 0, None),
        ],
    },

    # ── Functional — deductions ────────────────────────────────────
    "fn_volume_button": {
        "text": "Volume button",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-VOLUME-BTN",
        "options": [
            ("present", "Working", None, 0, None),
            ("missing", "Missing or not working", None, 2, 5),
        ],
    },
    "fn_power_button": {
        "text": "Power button",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-POWER-BTN",
        "options": [
            ("present", "Working", None, 0, None),
            ("missing", "Missing or not working", None, 2, 3),
        ],
    },
    "fn_sim_tray": {
        "text": "SIM tray",
        "purpose": DEDUCTION, "category": "Physical", "fault_code": "FAULT-SIM-TRAY",
        "options": [
            ("available", "Available and intact", None, 0, None),
            ("broken", "Broken or missing", None, 0, None),  # rate not on the sheet
        ],
    },
    "fn_ear_speaker": {
        "text": "Ear speaker / receiver",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-RECEIVER",
        "options": [
            ("working", "Working", None, 0, None),
            ("not_working", "Not working or low", None, 3, 5),
        ],
    },
    "fn_gps": {
        "text": "GPS",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-GPS",
        "options": [
            ("working", "Working", None, 0, None),
            ("not_working", "Not working", None, 2, 4),
        ],
    },
    "fn_face_id": {
        "text": "Face ID unlock test",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-FACE-ID",
        "options": [
            ("working", "Face ID is working", None, 0, None),
            ("not_working", "Face ID is not working", None, 25, None),
            ("not_present", "Face ID not present on this model", None, 0, None),
        ],
    },
    "fn_finger_touch": {
        "text": "Fingerprint / touch unlock test",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-FINGERPRINT",
        "options": [
            ("working", "Working", None, 0, None),
            ("not_working", "Not working", None, 5, 8),
            ("not_present", "Not present on this model", None, 0, None),
        ],
    },
    "fn_hinge": {
        "text": "Do the hinges open and fold properly?",
        "purpose": DEDUCTION, "category": "Physical", "fault_code": "FAULT-HINGE",
        "options": [
            ("working", "Yes, opens and folds properly", None, 0, None),
            ("not_working", "No, does not open or fold properly", None, 15, None),
        ],
    },

    # ── Audio, radio and sensors — deductions ──────────────────────
    # Every rate below is on the depreciation sheet. They had no question, so
    # the sheet's numbers were unreachable and the faults went uncharged.
    "fn_loudspeaker": {
        "text": "Loudspeaker",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-LOUDSPEAKER",
        "options": [
            ("working", "Working", None, 0, None),
            ("not_working", "Faulty, distorted or silent", None, 5, 7),
        ],
    },
    "fn_microphone": {
        "text": "Microphone",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-MICROPHONE",
        "options": [
            ("working", "Working", None, 0, None),
            ("not_working", "Not working", None, 5, 7),
        ],
    },
    "fn_ringer": {
        "text": "Does the phone ring on an incoming call?",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-RINGER",
        "options": [
            ("working", "Rings normally", None, 0, None),
            ("not_working", "Does not ring", None, 2, 4),
        ],
    },
    "fn_silent_button": {
        "text": "Ring / silent switch",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-SILENT-BTN",
        "options": [
            ("working", "Working", None, 0, None),
            ("not_working", "Not working", None, 5, None),
        ],
    },
    "fn_wifi": {
        "text": "Wi-Fi",
        "purpose": DEDUCTION, "category": "Network", "fault_code": "FAULT-WIFI",
        "options": [
            ("working", "Connects normally", None, 0, None),
            ("not_working", "Not working", None, 5, 7),
        ],
    },
    "fn_bluetooth": {
        "text": "Bluetooth",
        "purpose": DEDUCTION, "category": "Network", "fault_code": "FAULT-BLUETOOTH",
        "options": [
            ("working", "Pairs normally", None, 0, None),
            ("not_working", "Not working", None, 5, 7),
        ],
    },
    "fn_charging_port": {
        "text": "Charging port",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-CHARGING-PORT",
        "options": [
            ("working", "Charges normally", None, 0, None),
            ("not_working", "Not charging, or loose", None, 7, 8),
        ],
    },
    "fn_vibrator": {
        "text": "Vibration motor",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-VIBRATOR",
        "options": [
            ("working", "Working", None, 0, None),
            ("not_working", "Not working", None, 2, 4),
        ],
    },
    "fn_sensor": {
        "text": "Proximity and motion sensors (auto-rotate, screen-off on call)",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-SENSOR",
        "options": [
            ("working", "Working", None, 0, None),
            ("not_working", "Not working", None, 5, 7),
        ],
    },
    "fn_flash": {
        "text": "Camera flash",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-FLASH",
        "options": [
            ("working", "Working", None, 0, None),
            ("not_working", "Not working", None, 7, 8),
        ],
    },
    "fn_spen": {
        "text": "S-Pen",
        "purpose": DEDUCTION, "category": "Accessories", "fault_code": "FAULT-SPEN",
        "options": [
            ("working", "Present and working", None, 0, None),
            ("missing", "Missing or not working", None, 10, None),
            # Most Android handsets have no stylus; this keeps one question
            # usable across the range rather than needing a Samsung-only set.
            ("not_applicable", "Model has no S-Pen", None, 0, None),
        ],
    },
    "fn_headphone_jack": {
        "text": "Headphone jack",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-HEADPHONE-JACK",
        "options": [
            ("working", "Working", None, 0, None),
            ("not_working", "Not working", None, 0, None),  # rate not on the sheet
            ("not_present", "Model has no headphone jack", None, 0, None),
        ],
    },

    # ── History and provenance — deductions ────────────────────────
    # Not on the depreciation sheet, but standard on Cashify and Cashkr:
    # they change what the device can be resold as, so they are recorded and
    # priced rather than discovered later in refurb.
    "cond_water_damage": {
        "text": "Any sign of water or liquid damage?",
        "purpose": DEDUCTION, "category": "Physical", "fault_code": "FAULT-WATER-DAMAGE",
        "options": [
            ("none", "No sign of liquid damage", None, 0, None),
            ("damaged", "Liquid damage indicator triggered, or corrosion visible",
             None, 0, None),  # rate not on the sheet
        ],
    },
    "hist_previously_repaired": {
        "text": "Has the device been opened or repaired before?",
        "purpose": DEDUCTION, "category": "Diagnosis", "fault_code": "FAULT-PRIOR-REPAIR",
        "options": [
            ("no", "Never opened", None, 0, None),
            ("authorised", "Repaired at an authorised service centre", None, 0, None),
            ("unauthorised", "Repaired outside the authorised network", None, 0, None),
        ],
    },

    # ── SIM / network — deductions ─────────────────────────────────
    "sim_1_working": {
        "text": "Is SIM slot 1 working?",
        "purpose": DEDUCTION, "category": "Network", "fault_code": "FAULT-SIM-1",
        "options": [
            ("yes", "Working", None, 0, None),
            ("no", "Not working", None, 0, None),  # rate not on the sheet
        ],
    },
    "sim_2_working": {
        "text": "Is SIM slot 2 working?",
        "purpose": DEDUCTION, "category": "Network", "fault_code": "FAULT-SIM-2",
        "options": [
            ("yes", "Working", None, 0, None),
            ("no", "Not working", None, 0, None),  # rate not on the sheet
            ("not_present", "Single-SIM device", None, 0, None),
        ],
    },
    "sim_calls": {
        "text": "Can the device make and receive calls?",
        "purpose": GRADING, "category": "Network", "fault_code": "FAULT-CALLS",
        "options": [
            ("yes", "Yes", None, 0, None),
            ("no", "No", "D", 0, None),
        ],
    },
    "sim_esim_support": {
        "text": "How many eSIMs does the device support?",
        "purpose": DEDUCTION, "category": "Network", "fault_code": "FAULT-ESIM-COUNT",
        "options": [
            ("single_esim", "Single eSIM", None, 0, None),
            ("dual_esim", "Dual eSIM", None, 0, None),
            ("both_physical", "Both physical SIM", None, 0, None),
        ],
    },

    # ── Camera — deductions ────────────────────────────────────────
    # The sheet prices a degraded camera (5/8) and a dead one (10/12)
    # separately. They are rungs on one question rather than two questions, so
    # a dead camera cannot be charged as both.
    "cam_front": {
        "text": "Front camera",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-CAMERA-FRONT",
        "options": [
            ("no_issue", "Working normally", None, 0, None),
            ("issue", "Blurred, spotted or distorted", None, 5, 8),
            ("not_working", "Not working at all", None, 10, 12),
        ],
    },
    "cam_back": {
        "text": "Back camera",
        "purpose": DEDUCTION, "category": "Functional", "fault_code": "FAULT-CAMERA-BACK",
        "options": [
            ("no_issue", "Working normally", None, 0, None),
            ("issue", "Blurred, spotted or distorted", None, 5, 8),
            ("not_working", "Not working at all", None, 10, 12),
        ],
    },
    "cam_glass": {
        "text": "Is the camera glass broken?",
        "purpose": DEDUCTION, "category": "Physical", "fault_code": "FAULT-CAMERA-GLASS",
        "options": [
            ("no", "Intact", None, 0, None),
            ("yes", "Broken", None, 3, 5),
        ],
    },

    # ── Apple unknown parts — deductions ───────────────────────────
    "apl_unknown_display": {
        "text": "Is the display part reported as unknown?",
        "purpose": DEDUCTION, "category": "Diagnosis", "fault_code": "FAULT-UNKNOWN-DISPLAY",
        "options": [
            ("known", "No, the display part is genuine", None, 0, None),
            ("unknown", "Yes, the display part is unknown", None, 0, None),  # rate not on the sheet
        ],
    },
    "apl_unknown_camera": {
        "text": "Is the camera part reported as unknown?",
        "purpose": DEDUCTION, "category": "Diagnosis", "fault_code": "FAULT-UNKNOWN-CAMERA",
        "options": [
            ("known", "No, the camera part is genuine", None, 0, None),
            ("unknown", "Yes, the camera part is unknown", None, 3, None),
        ],
    },
    "apl_unknown_battery": {
        "text": "Is the battery part reported as unknown?",
        "purpose": DEDUCTION, "category": "Diagnosis", "fault_code": "FAULT-UNKNOWN-BATTERY",
        "options": [
            ("known", "No, the battery part is genuine", None, 0, None),
            ("unknown", "Yes, the battery part is unknown", None, 4, None),
        ],
    },

    # ── Battery — deductions, one question per platform ────────────
    "bat_health_ios": {
        "text": "Battery health",
        "purpose": DEDUCTION, "category": "Battery", "fault_code": "FAULT-BATTERY",
        "options": [
            ("above_85", "Above 85% — good", None, 0, None),
            ("80_to_85", "80–85% — moderate", None, 4, None),
            ("below_80", "Below 80% — service required or swollen", None, 7, None),
        ],
    },
    "bat_condition_android": {
        "text": "Battery condition",
        "purpose": DEDUCTION, "category": "Battery", "fault_code": "FAULT-BATTERY",
        "options": [
            ("healthy", "Healthy", None, 0, None),
            ("bulged", "Bulged or not working", None, 5, None),
        ],
    },

    # ── Commercial — deductions ────────────────────────────────────
    # Charger, box and bill are asked separately, as Cashify and Cashkr do.
    # A single combined question needs one option per combination, and a
    # customer who brings two of the three should not be scored as if they
    # brought neither. Each is its own fault, so they never double-charge.
    #
    # The depreciation sheet does not price accessories; the 5% per missing
    # item carries over from the rates already live on this site rather than
    # being invented here.
    "com_charger": {
        "text": "Original charger",
        "purpose": DEDUCTION, "category": "Accessories", "fault_code": "FAULT-NO-CHARGER",
        "options": [
            ("available", "Available", None, 0, None),
            ("missing", "Not available", None, 5, None),
        ],
    },
    "com_box": {
        "text": "Original box with matching IMEI",
        "purpose": DEDUCTION, "category": "Accessories", "fault_code": "FAULT-NO-BOX",
        "options": [
            ("available", "Available", None, 0, None),
            ("missing", "Not available", None, 5, None),
        ],
    },
    "com_bill": {
        "text": "Original bill or invoice with matching IMEI",
        "purpose": DEDUCTION, "category": "Accessories", "fault_code": "FAULT-NO-BILL",
        "options": [
            ("available", "Available", None, 0, None),
            ("missing", "Not available", None, 5, None),
        ],
    },
    "com_purchased_in_india": {
        "text": "Was the device purchased in India?",
        "purpose": DEDUCTION, "category": "General", "fault_code": "FAULT-PURCHASED-ABROAD",
        "options": [
            ("india", "Purchased in India", None, 0, None),
            ("abroad", "Not purchased in India", None, 8, None),
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
    # Buttons and ports
    "fn_volume_button", "fn_power_button", "fn_sim_tray", "fn_charging_port",
    "fn_headphone_jack",
    # Audio
    "fn_ear_speaker", "fn_loudspeaker", "fn_microphone", "fn_ringer",
    # Radios and sensors
    "fn_wifi", "fn_bluetooth", "fn_gps", "fn_sensor", "fn_vibrator",
    # Biometrics
    "fn_finger_touch",
]
_CAMERA_CORE = ["cam_front", "cam_back", "cam_glass", "fn_flash"]
_SIM_CORE = ["sim_1_working", "sim_2_working", "sim_calls", "sim_esim_support"]
_HISTORY = ["cond_water_damage", "hist_previously_repaired"]
_COMMERCIAL = ["com_charger", "com_box", "com_bill", "com_purchased_in_india"]

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
            + _FUNCTIONAL_CORE + ["fn_silent_button", "fn_face_id"]
            + _CAMERA_CORE
            + ["apl_unknown_display", "apl_unknown_camera", "apl_unknown_battery"]
            + ["bat_health_ios"]
            + _HISTORY
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
            + _FUNCTIONAL_CORE + ["fn_spen"]
            + _CAMERA_CORE
            + ["bat_condition_android"]
            + _HISTORY
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
            + _FUNCTIONAL_CORE + ["fn_spen"]
            + _CAMERA_CORE
            + ["bat_condition_android"]
            + _HISTORY
            + _COMMERCIAL
        ),
    },
]


# Codes this catalogue used to define and no longer does. The seeder disables
# them so a question it created cannot linger, enabled but unreachable, after
# being restructured — com_accessories became the separate charger / box / bill
# questions Cashify and Cashkr ask for individually.
RETIRED_CODES = ["com_accessories"]


# Questions that only make sense on one platform. Enforced by
# validate_catalogue() and, at runtime, by BuybackQuestionSet.validate().
BRAND_FAMILY_ONLY = {
    "elig_icloud_lock": "Apple",
    "elig_country_lock": "Apple",
    "fn_face_id": "Apple",
    "apl_unknown_display": "Apple",
    "apl_unknown_camera": "Apple",
    "apl_unknown_battery": "Apple",
    "bat_health_ios": "Apple",
    "fn_silent_button": "Apple",
    "elig_account_removed": "Android",
    "bat_condition_android": "Android",
}


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
        if not any(opt[3] or opt[4] for opt in q["options"]):
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

        # A platform-specific question in the wrong set is how a 25% Face ID
        # rate would reach an Android quote.
        for code in codes:
            only = BRAND_FAMILY_ONLY.get(code)
            if only and spec["brand_family"] != only:
                raise ValueError(
                    f"{spec['set_name']} ({spec['brand_family']}) includes "
                    f"{code}, which is {only}-only"
                )

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
