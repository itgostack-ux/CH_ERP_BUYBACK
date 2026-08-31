"""Grade is decided by condition, then selects the price cell.

The engine used to run this backwards: it priced everything off the Grade A
cell, subtracted every fault it could find, then read a grade back off the
resulting number by snapping it to the nearest bracket. The grade an inspector
saw was an artefact of arithmetic, and the Final Condition Grade dropdown could
not move the payout at all.

These tests pin the corrected direction, and the set routing that decides which
questions a given model is even asked.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from buyback.buyback.doctype.buyback_question_set.buyback_question_set import (
    get_device_profile,
    resolve_set_for_item,
)
from buyback.buyback.pricing.engine import (
    calculate_estimated_price,
    resolve_grade_from_answers,
)
from buyback.setup.question_catalogue import QUESTIONS, SETS, validate_catalogue

ITEM_CODE = "_TEST_GRADE_ITEM"
A_OOW = 8000.0
C_OOW = 6000.0
D_OOW = 5000.0
SCRAP = 700.0


def _ensure_priced_item():
    if not frappe.db.exists("Item", ITEM_CODE):
        frappe.get_doc({
            "doctype": "Item",
            "item_code": ITEM_CODE,
            "item_name": "Grade Resolution Test Handset",
            "item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
            "stock_uom": "Nos",
            "is_stock_item": 0,
        }).insert(ignore_permissions=True)

    name = frappe.db.get_value("Buyback Price Master", {"item_code": ITEM_CODE}, "name")
    doc = (frappe.get_doc("Buyback Price Master", name) if name
           else frappe.new_doc("Buyback Price Master"))
    doc.item_code = ITEM_CODE
    doc.is_active = 1
    doc.a_grade_oow_11 = A_OOW
    doc.c_grade_oow_11 = C_OOW
    doc.d_grade_oow_11 = D_OOW
    doc.scrap_price = SCRAP
    doc.flags.from_ready_reckoner = True
    doc.save(ignore_permissions=True)


def _answers(**kwargs):
    return [{"question_code": code, "answer_value": value} for code, value in kwargs.items()]


class TestGradeFromCondition(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _ensure_priced_item()

    def _price(self, **kwargs):
        args = {
            "item_code": ITEM_CODE,
            "grade": None,
            "warranty_status": "Out of Warranty",
            "device_age_months": "12+ Months",
        }
        args.update(kwargs)
        return calculate_estimated_price(**args)

    # ── grade derivation ────────────────────────────────────────────
    def test_worst_answer_decides_the_grade(self):
        cases = [
            ("A", dict(scr_spots="none", body_scratches="none")),
            ("B", dict(scr_cracks_scratches="upto_5_under_1cm", body_scratches="none")),
            ("C", dict(scr_cracks_scratches="upto_5_under_1cm", body_scratches="upto_15_1cm")),
            ("D", dict(scr_spots="coloured")),
            ("D", dict(scr_is_copy="copy")),
            ("D", dict(scr_flickering="flickering")),
            ("D", dict(body_bent="bent")),
            # One serious fault outranks any number of clean answers.
            ("D", dict(scr_spots="none", body_scratches="none", scr_lines="lines")),
        ]
        for expected, answers in cases:
            with self.subTest(expected=expected, answers=answers):
                self.assertEqual(resolve_grade_from_answers(_answers(**answers)), expected)

    def test_deduction_answers_never_move_the_grade(self):
        """A flat battery is a deduction, not a downgrade."""
        grade = resolve_grade_from_answers(_answers(
            fn_gps="not_working", cam_front="issue", fn_ear_speaker="not_working",
        ))
        self.assertEqual(grade, "A")

    # ── grade selects the cell ──────────────────────────────────────
    def test_grade_selects_the_price_cell(self):
        clean = self._price(responses=_answers(scr_spots="none", body_scratches="none"))
        self.assertEqual(clean["grade_letter"], "A")
        self.assertEqual(flt(clean["base_price"]), A_OOW)

        cracked = self._price(responses=_answers(scr_lines="lines"))
        self.assertEqual(cracked["grade_letter"], "D")
        self.assertEqual(
            flt(cracked["base_price"]), D_OOW,
            "a Grade D device is still being priced off the Grade A cell",
        )

    def test_deductions_are_a_percentage_of_the_graded_price(self):
        """Locked decision: percentages apply to the selected grade's price.

        So one broken GPS costs fewer rupees on a battered handset than on a
        pristine one.
        """
        on_d = self._price(responses=_answers(scr_lines="lines", fn_gps="not_working"))
        on_a = self._price(responses=_answers(scr_lines="none", fn_gps="not_working"))

        self.assertEqual(flt(on_d["total_deductions"]), D_OOW * 0.02)
        self.assertEqual(flt(on_a["total_deductions"]), A_OOW * 0.02)
        self.assertGreater(
            flt(on_a["total_deductions"]), flt(on_d["total_deductions"]))

    def test_a_light_fault_on_a_d_device_is_not_scrap(self):
        result = self._price(responses=_answers(scr_lines="lines", fn_gps="not_working"))
        self.assertFalse(result.get("is_scrap"))
        self.assertEqual(result["grade_letter"], "D")

    def test_a_quote_with_no_grading_answers_keeps_its_grade(self):
        """API and re-price paths pass a stored grade and no answers."""
        result = self._price(grade="C")
        self.assertEqual(result["grade_letter"], "C")
        self.assertEqual(flt(result["base_price"]), C_OOW)

    # ── set routing ─────────────────────────────────────────────────
    def test_catalogue_has_no_repeated_question_or_fault(self):
        validate_catalogue()

    def test_sets_ask_each_fault_once(self):
        for spec in SETS:
            with self.subTest(spec["set_name"]):
                rows = frappe.get_all(
                    "Buyback Question Set Item",
                    filters={"parent": spec["set_name"]}, pluck="question",
                )
                self.assertEqual(len(rows), len(set(rows)), "a question appears twice")

                faults = [
                    f for f in frappe.get_all(
                        "Buyback Question Bank",
                        filters={"name": ["in", rows], "question_purpose": "Deduction"},
                        pluck="fault_code",
                    ) if f
                ]
                repeated = sorted({f for f in faults if faults.count(f) > 1})
                self.assertFalse(repeated, f"one fault charged twice: {repeated}")

    def test_apple_only_questions_stay_off_android(self):
        rows = frappe.get_all(
            "Buyback Question Set Item",
            filters={"parent": "Android Handset"}, pluck="question",
        )
        codes = set(frappe.get_all(
            "Buyback Question Bank", filters={"name": ["in", rows]}, pluck="question_code"))

        for apple_only in (
            "fn_face_id", "elig_icloud_lock", "elig_country_lock",
            "apl_unknown_display", "apl_unknown_camera", "apl_unknown_battery",
            "bat_health_ios",
        ):
            self.assertNotIn(apple_only, codes, f"{apple_only} leaked onto Android")

        self.assertIn("bat_condition_android", codes)
        self.assertIn("elig_account_removed", codes)

    def test_foldable_questions_only_appear_on_the_foldable_set(self):
        for set_name, expected in (
            ("Foldable Handset", True),
            ("Android Handset", False),
            ("Apple Handset", False),
        ):
            rows = frappe.get_all(
                "Buyback Question Set Item", filters={"parent": set_name}, pluck="question")
            codes = set(frappe.get_all(
                "Buyback Question Bank", filters={"name": ["in", rows]}, pluck="question_code"))
            for foldable_only in ("scr_inner_display", "scr_outer_display", "fn_hinge"):
                self.assertEqual(
                    foldable_only in codes, expected,
                    f"{foldable_only} presence wrong on {set_name}",
                )

    def test_device_profile_routes_to_the_right_set(self):
        original = frappe.db.get_value("Item", ITEM_CODE, "ch_is_foldable")
        try:
            frappe.db.set_value("Item", ITEM_CODE, "ch_is_foldable", 0)
            family, form_factor = get_device_profile(ITEM_CODE)
            self.assertEqual(form_factor, "Standard")
            self.assertEqual(resolve_set_for_item(ITEM_CODE), f"{family} Handset")

            frappe.db.set_value("Item", ITEM_CODE, "ch_is_foldable", 1)
            self.assertEqual(get_device_profile(ITEM_CODE)[1], "Foldable")
        finally:
            frappe.db.set_value("Item", ITEM_CODE, "ch_is_foldable", original or 0)

    def test_every_grading_option_declares_a_grade_or_none(self):
        """A grading ladder with no forced grade cannot move anything."""
        for code, spec in QUESTIONS.items():
            if spec["purpose"] != "Grading":
                continue
            with self.subTest(code):
                forced = {opt[2] for opt in spec["options"]}
                self.assertTrue(
                    forced - {None},
                    f"{code} is a grading question but no option forces a grade",
                )
                self.assertIn(None, forced, f"{code} has no clean answer")
