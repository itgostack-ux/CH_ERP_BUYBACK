"""The same fault costs a different amount on Apple.

The depreciation sheet prices roughly twenty faults differently by platform — a
broken charging port is 7% on Android and 8% on an iPhone, Face ID is 25% and
has no Android equivalent at all. Before this, `Buyback Question Option` held a
single percentage and the codebase contained no reference to Apple or Android
anywhere, so no Apple rate on the sheet could be expressed.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from buyback.buyback.pricing.engine import calculate_estimated_price
from buyback.setup.question_catalogue import BRAND_FAMILY_ONLY, QUESTIONS

BASE = 10000.0
ANDROID_ITEM = "_TEST_RATE_ANDROID"
APPLE_ITEM = "_TEST_RATE_APPLE"


def _ensure_item(code, sub_category):
    if not frappe.db.exists("Item", code):
        frappe.get_doc({
            "doctype": "Item",
            "item_code": code,
            "item_name": f"Rate test {code}",
            "item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
            "stock_uom": "Nos",
            "is_stock_item": 0,
        }).insert(ignore_permissions=True)
    # Brand family is read off the sub-category, the same signal the question
    # sets route on. Fall back to the brand when the sub-category master has no
    # such record, so the fixture works on a site seeded either way.
    if frappe.db.exists("CH Sub Category", sub_category):
        frappe.db.set_value("Item", code, "ch_sub_category", sub_category)
    elif "ios" in sub_category.lower():
        frappe.db.set_value("Item", code, "brand", _ensure_apple_brand())

    name = frappe.db.get_value("Buyback Price Master", {"item_code": code}, "name")
    doc = (frappe.get_doc("Buyback Price Master", name) if name
           else frappe.new_doc("Buyback Price Master"))
    doc.item_code = code
    doc.is_active = 1
    doc.a_grade_oow_11 = BASE
    doc.flags.from_ready_reckoner = True
    doc.save(ignore_permissions=True)


def _ensure_apple_brand():
    if not frappe.db.exists("Brand", "Apple"):
        frappe.get_doc({"doctype": "Brand", "brand": "Apple"}).insert(ignore_permissions=True)
    return "Apple"


class TestBrandFamilyRates(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _ensure_item(ANDROID_ITEM, "Smart Phones-Android Phones")
        _ensure_item(APPLE_ITEM, "Smart Phones-iOS Phones")

        # Fail here rather than as a confusing rate mismatch further down: if
        # the fixture does not actually read as Apple, every rate assertion
        # below is meaningless.
        from buyback.buyback.doctype.buyback_question_set.buyback_question_set import (
            get_device_profile,
        )
        assert get_device_profile(APPLE_ITEM)[0] == "Apple", (
            f"{APPLE_ITEM} resolves to "
            f"{get_device_profile(APPLE_ITEM)[0]}, not Apple — fixture is wrong"
        )
        assert get_device_profile(ANDROID_ITEM)[0] == "Android", (
            f"{ANDROID_ITEM} resolves to "
            f"{get_device_profile(ANDROID_ITEM)[0]}, not Android — fixture is wrong"
        )

    def _price(self, item, **answers):
        return calculate_estimated_price(
            item_code=item, grade="A",
            warranty_status="Out of Warranty", device_age_months="12+ Months",
            responses=[{"question_code": c, "answer_value": v} for c, v in answers.items()],
        )

    def test_apple_pays_the_apple_rate(self):
        """Every option the sheet prices differently, checked both ways."""
        cases = [
            ("fn_volume_button", "missing", 2, 5),
            ("fn_power_button", "missing", 2, 3),
            ("fn_ear_speaker", "not_working", 3, 5),
            ("fn_gps", "not_working", 2, 4),
            ("fn_finger_touch", "not_working", 5, 8),
            ("cam_front", "issue", 5, 8),
            ("cam_back", "issue", 5, 8),
            ("cam_glass", "yes", 3, 5),
        ]
        for code, answer, android_pct, apple_pct in cases:
            with self.subTest(code):
                android = self._price(ANDROID_ITEM, **{code: answer})
                apple = self._price(APPLE_ITEM, **{code: answer})

                self.assertEqual(android["brand_family"], "Android")
                self.assertEqual(apple["brand_family"], "Apple")
                self.assertEqual(flt(android["total_deductions"]), BASE * android_pct / 100)
                self.assertEqual(flt(apple["total_deductions"]), BASE * apple_pct / 100)
                self.assertGreater(
                    flt(apple["total_deductions"]), flt(android["total_deductions"]),
                    f"{code} should cost more on Apple",
                )

    def test_a_blank_apple_rate_falls_back_to_the_standard_one(self):
        """Blank means 'same as Android', never 'free on Apple'.

        Purchased-abroad is 8% for both platforms on the sheet, so it carries
        no Apple column.
        """
        android = self._price(ANDROID_ITEM, com_purchased_in_india="abroad")
        apple = self._price(APPLE_ITEM, com_purchased_in_india="abroad")
        self.assertEqual(flt(android["total_deductions"]), BASE * 0.08)
        self.assertEqual(flt(apple["total_deductions"]), BASE * 0.08)

    def test_apple_only_rates_cannot_reach_an_android_quote(self):
        """Face ID is 25%. An Android handset must never carry it."""
        result = self._price(ANDROID_ITEM, fn_face_id="not_working")
        self.assertEqual(flt(result["total_deductions"]), 0)

    def test_platform_questions_are_flagged_on_the_bank(self):
        for code, family in BRAND_FAMILY_ONLY.items():
            with self.subTest(code):
                self.assertEqual(
                    frappe.db.get_value(
                        "Buyback Question Bank",
                        {"question_code": code}, "applies_to_brand_family"),
                    family,
                )

    def test_a_set_rejects_a_question_from_the_other_platform(self):
        """The guard that stops a 25% Face ID rate reaching Android."""
        doc = frappe.get_doc("Buyback Question Set", "Android Handset")
        face_id = frappe.db.get_value(
            "Buyback Question Bank", {"question_code": "fn_face_id"}, "name")
        doc.append("questions", {"question": face_id, "display_order": 999})
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
        doc.reload()

    def test_every_sheet_rate_is_reachable(self):
        """Each row of the depreciation sheet has a question that charges it.

        Twelve rows had none — wifi, bluetooth, microphone, sensor, flash,
        charging port, ringer, silent switch, S-Pen, loudspeaker and the two
        camera-not-working tiers. Their rates existed on paper and could never
        be applied to a quote.
        """
        sheet = {
            # (android, apple): the option that must carry it
            "FAULT-VOLUME-BTN":     (2, 5),
            "FAULT-POWER-BTN":      (2, 3),
            "FAULT-RECEIVER":       (3, 5),
            "FAULT-GPS":            (2, 4),
            "FAULT-FINGERPRINT":    (5, 8),
            "FAULT-CAMERA-GLASS":   (3, 5),
            "FAULT-LOUDSPEAKER":    (5, 7),
            "FAULT-MICROPHONE":     (5, 7),
            "FAULT-RINGER":         (2, 4),
            "FAULT-WIFI":           (5, 7),
            "FAULT-BLUETOOTH":      (5, 7),
            "FAULT-CHARGING-PORT":  (7, 8),
            "FAULT-VIBRATOR":       (2, 4),
            "FAULT-SENSOR":         (5, 7),
            "FAULT-FLASH":          (7, 8),
            "FAULT-SILENT-BTN":     (5, 5),
            "FAULT-SPEN":           (10, 10),
            "FAULT-HINGE":          (15, 15),
            "FAULT-PURCHASED-ABROAD": (8, 8),
            "FAULT-FACE-ID":        (25, 25),
        }
        by_fault = {q["fault_code"]: (code, q) for code, q in QUESTIONS.items()}

        for fault, (android, apple) in sheet.items():
            with self.subTest(fault):
                self.assertIn(fault, by_fault, f"{fault} has no question")
                _code, spec = by_fault[fault]
                worst_android = max(o[3] for o in spec["options"])
                worst_apple = max((o[4] if o[4] is not None else o[3])
                                  for o in spec["options"])
                self.assertEqual(worst_android, android)
                self.assertEqual(worst_apple, apple)

    def test_camera_tiers_cover_both_sheet_rows(self):
        """"Camera issue" (5/8) and "camera not working" (10/12) are rungs on
        one question, so a dead camera cannot be charged as both."""
        for code in ("cam_front", "cam_back"):
            with self.subTest(code):
                rates = {o[0]: (o[3], o[4]) for o in QUESTIONS[code]["options"]}
                self.assertEqual(rates["issue"], (5, 8))
                self.assertEqual(rates["not_working"], (10, 12))

    def test_deduction_rates_match_the_depreciation_sheet(self):
        """Catalogue is the source of truth; this pins it to the sheet."""
        expected = {
            ("fn_face_id", "not_working"): (25, None),
            ("fn_hinge", "not_working"): (15, None),
            ("com_purchased_in_india", "abroad"): (8, None),
            ("apl_unknown_camera", "unknown"): (3, None),
            ("apl_unknown_battery", "unknown"): (4, None),
            ("bat_health_ios", "80_to_85"): (4, None),
            ("bat_health_ios", "below_80"): (7, None),
            ("bat_condition_android", "bulged"): (5, None),
        }
        for (code, value), (android, apple) in expected.items():
            with self.subTest(f"{code}.{value}"):
                option = next(
                    o for o in QUESTIONS[code]["options"] if o[0] == value)
                self.assertEqual(option[3], android)
                self.assertEqual(option[4], apple)
