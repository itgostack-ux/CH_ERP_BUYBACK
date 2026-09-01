"""Pricing integrity guards for the buyback engine.

Each test here pins down a defect that shipped silently — the engine produced a
number, nobody saw an error, and the number was wrong. They are deliberately
written against the engine's public entry point rather than its helpers, so a
refactor that reintroduces the behaviour still trips them.

The suite builds its own Price Master row rather than relying on site data, so
it runs the same on a fresh site as on a restored one.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from buyback.buyback.pricing.engine import _clamp_deductions, calculate_estimated_price

ITEM_CODE = "_TEST_BUYBACK_PRICING_ITEM"
BASE_IW_0_3 = 10000.0
BASE_C_IW_0_3 = 7000.0
BASE_OOW_11 = 7800.0
FLOOR_OOW_11 = 4928.0
SCRAP = 900.0
DEAD = 400.0


def _ensure_item():
    if frappe.db.exists("Item", ITEM_CODE):
        return ITEM_CODE
    from buyback.tests import test_item_compliance_fields

    frappe.get_doc({
        "doctype": "Item",
        "item_code": ITEM_CODE,
        "item_name": "Buyback Pricing Test Handset",
        "item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
        "stock_uom": "Nos",
        "is_stock_item": 0,
        **test_item_compliance_fields(),
    }).insert(ignore_permissions=True)
    return ITEM_CODE


def _ensure_price_master(scrap=SCRAP, dead=DEAD):
    """Create or reset the test Price Master row.

    Price fields are maker/checker gated on this doctype, so the write goes
    through the same `from_ready_reckoner` flag the Ready Reckoner batch uses.
    """
    name = frappe.db.get_value("Buyback Price Master", {"item_code": ITEM_CODE}, "name")
    doc = (frappe.get_doc("Buyback Price Master", name) if name
           else frappe.new_doc("Buyback Price Master"))
    doc.item_code = _ensure_item()
    doc.is_active = 1
    doc.a_grade_iw_0_3 = BASE_IW_0_3
    doc.c_grade_iw_0_3 = BASE_C_IW_0_3
    doc.a_grade_oow_11 = BASE_OOW_11
    doc.d_grade_oow_11 = FLOOR_OOW_11
    doc.scrap_price = scrap
    doc.phone_dead_price = dead
    doc.flags.from_ready_reckoner = True
    doc.save(ignore_permissions=True)
    return doc.name


def _ensure_question(code, diagnosis_type, options, fault_code=None, purpose=None):
    name = frappe.db.get_value("Buyback Question Bank", {"question_code": code}, "name")
    doc = (frappe.get_doc("Buyback Question Bank", name) if name
           else frappe.new_doc("Buyback Question Bank"))
    doc.question_text = f"Pricing test — {code}"
    doc.question_code = code
    doc.fault_code = fault_code
    doc.diagnosis_type = diagnosis_type
    if purpose:
        doc.question_purpose = purpose
    doc.question_type = "Single Select"
    doc.disabled = 0
    doc.set("options", [])
    for option in options:
        value, impact = option[:2]
        doc.append("options", {
            "option_value": value, "option_label": value,
            "price_impact_percent": impact,
            "forces_grade": option[2] if len(option) > 2 else None,
        })
    doc.save(ignore_permissions=True)
    return doc.name


class TestPricingIntegrity(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _ensure_price_master()
        cls.grade_a = frappe.db.get_value("Grade Master", {"grade_name": "A"}, "name")

        # Two questions describing ONE fault — the shape that used to
        # double-charge: an automated test and its customer-question twin.
        _ensure_question("_test_speaker_auto", "Automated Test",
                         [("Pass", 0), ("Fail", -2)], fault_code="_TEST-FAULT-SPEAKER")
        _ensure_question("_test_speaker_cust", "Customer Question",
                         [("Yes", -2), ("No", 0)], fault_code="_TEST-FAULT-SPEAKER")
        # A genuinely different fault, to prove grouping is not over-eager.
        _ensure_question("_test_mic_auto", "Automated Test",
                         [("Pass", 0), ("Fail", -2)], fault_code="_TEST-FAULT-MIC")
        # A large impact used to blow past the deduction cap.
        _ensure_question("_test_heavy", "Customer Question",
                         [("Yes", -90), ("No", 0)])
        # Schema truth (buyback_question_option.json): `forces_grade` now
        # offers only "", "B", "C", "D" — "A" was removed as an option
        # because A is the engine's default (GRADE_ORDER[0]); an option that
        # "forces" A forces nothing. A clean answer therefore leaves
        # forces_grade BLANK and the grade stays A unless another answer
        # caps it lower. Do not resurrect the removed "A" option here.
        cls.preview_grade_question = _ensure_question(
            "_test_preview_grade", "Customer Question",
            [("Clean", 0, None), ("Worn", 0, "C")],
            purpose="Grading",
        )
        frappe.db.commit()

    def _price(self, **kwargs):
        args = {
            "item_code": ITEM_CODE,
            "grade": self.grade_a,
            "warranty_status": "In Warranty",
            "device_age_months": "0-3 Months",
        }
        args.update(kwargs)
        return calculate_estimated_price(**args)

    # ── B4 / B5 ─────────────────────────────────────────────────────
    def test_pricing_without_band_inputs_is_refused(self):
        """Missing age used to resolve to 0 and select the HIGHEST band."""
        for kwargs in (
            {"warranty_status": None, "device_age_months": None},
            {"warranty_status": "In Warranty", "device_age_months": None},
            {"warranty_status": None, "device_age_months": "0-3 Months"},
        ):
            with self.assertRaises(frappe.ValidationError):
                self._price(**kwargs)

    def test_each_band_selects_its_own_price(self):
        self.assertEqual(flt(self._price()["base_price"]), BASE_IW_0_3)
        self.assertEqual(
            flt(self._price(warranty_status="Out of Warranty",
                            device_age_months="12+ Months")["base_price"]),
            BASE_OOW_11,
        )

    def test_live_preview_normalizes_question_name_to_grading_code(self):
        """POS preview and assessment save must resolve the same grade."""
        import json

        from buyback.api import calculate_live_estimate

        result = calculate_live_estimate(
            item_code=ITEM_CODE,
            warranty_status="In Warranty",
            device_age_months="0-3 Months",
            responses=json.dumps([{
                "question": self.preview_grade_question,
                "answer_value": "Worn",
            }]),
            diagnostic_tests="[]",
        )
        self.assertEqual(result["grade"], "C")
        self.assertEqual(flt(result["base_price"]), BASE_C_IW_0_3)

    # ── B6 ──────────────────────────────────────────────────────────
    def test_unpriced_band_throws_instead_of_borrowing_a_lower_grade(self):
        """The A cell for IW 7-11 is blank on the test row.

        The engine used to substitute the B, then C, then D price and still
        call the result a Grade A base.
        """
        with self.assertRaises(frappe.ValidationError):
            self._price(warranty_status="In Warranty", device_age_months="7-11 Months")

    # ── B11 ─────────────────────────────────────────────────────────
    def test_one_fault_is_charged_once(self):
        result = self._price(
            diagnostic_tests=[{"test_code": "_test_speaker_auto", "result": "Fail"}],
            responses=[{"question_code": "_test_speaker_cust", "answer_value": "Yes"}],
        )
        self.assertEqual(len(result["deductions"]), 1, result["deductions"])
        self.assertEqual(flt(result["total_deductions"]), BASE_IW_0_3 * 0.02)

    def test_yes_means_diagnostic_pass_and_no_means_failure(self):
        yes = self._price(diagnostic_tests=[
            {"test_code": "_test_speaker_auto", "result": "Yes"},
        ])
        no = self._price(diagnostic_tests=[
            {"test_code": "_test_speaker_auto", "result": "No"},
        ])
        self.assertEqual(flt(yes["total_deductions"]), 0)
        self.assertEqual(flt(no["total_deductions"]), BASE_IW_0_3 * 0.02)

    def test_distinct_faults_still_stack(self):
        result = self._price(diagnostic_tests=[
            {"test_code": "_test_speaker_auto", "result": "Fail"},
            {"test_code": "_test_mic_auto", "result": "Fail"},
        ])
        self.assertEqual(len(result["deductions"]), 2)
        self.assertEqual(flt(result["total_deductions"]), BASE_IW_0_3 * 0.04)

    # ── B9 ──────────────────────────────────────────────────────────
    def test_deductions_are_capped_at_the_base_price(self):
        self.assertEqual(_clamp_deductions(48050, 10000), 10000)
        self.assertEqual(_clamp_deductions(500, 10000), 500)

    def test_stacked_faults_land_on_scrap_not_a_negative_quote(self):
        # 90% off the OOW base leaves 780, which is under the 900 scrap value.
        result = self._price(
            warranty_status="Out of Warranty", device_age_months="12+ Months",
            responses=[{"question_code": "_test_heavy", "answer_value": "Yes"}],
        )
        self.assertTrue(result.get("is_scrap"))
        self.assertEqual(flt(result["estimated_price"]), SCRAP)
        self.assertEqual(result["grade_letter"], "E")

    def test_a_light_fault_does_not_trip_the_scrap_floor(self):
        """The floor is the salvage value, not the band's lowest grade price.

        While every quote started from the Grade A cell, "below the lowest
        grade price" was a reasonable proxy for scrap. Once the grade selects
        the cell it stopped being one: a Grade D device starts at the D price,
        so any deduction at all would have tipped it into scrap.
        """
        result = self._price(
            warranty_status="Out of Warranty", device_age_months="12+ Months",
            diagnostic_tests=[{"test_code": "_test_speaker_auto", "result": "Fail"}],
        )
        self.assertFalse(result.get("is_scrap"))
        self.assertEqual(flt(result["estimated_price"]), BASE_OOW_11 * 0.98)

    # ── B10 ─────────────────────────────────────────────────────────
    def test_missing_phone_dead_price_is_named_not_quoted_as_zero(self):
        _ensure_price_master(scrap=0, dead=0)
        try:
            with self.assertRaises(frappe.ValidationError):
                self._price(is_phone_dead=True)
        finally:
            _ensure_price_master()

    def test_unset_scrap_price_never_silently_floors_a_quote(self):
        """With no scrap value configured the floor simply does not engage.

        It must not floor to ₹0 — that was the behaviour that offered a
        customer nothing and called it a quote. The deduction cap already
        keeps the remainder non-negative.
        """
        _ensure_price_master(scrap=0, dead=0)
        try:
            result = self._price(
                warranty_status="Out of Warranty", device_age_months="12+ Months",
                responses=[{"question_code": "_test_heavy", "answer_value": "Yes"}],
            )
            self.assertFalse(result.get("is_scrap"))
            self.assertEqual(flt(result["estimated_price"]), BASE_OOW_11 * 0.10)
        finally:
            _ensure_price_master()

    def test_phone_dead_returns_the_salvage_price_and_grade_f(self):
        result = self._price(is_phone_dead=True)
        self.assertEqual(flt(result["estimated_price"]), DEAD)
        self.assertEqual(result["grade_letter"], "F")

    # ── B2 ──────────────────────────────────────────────────────────
    def test_salvage_grades_exist_but_stay_out_of_the_picker(self):
        from buyback.api import get_grades

        for letter in ("E", "F"):
            row = frappe.db.get_value(
                "Grade Master", {"grade_name": letter},
                ["name", "is_salvage"], as_dict=True,
            )
            self.assertIsNotNone(row, f"Grade Master has no {letter}")
            self.assertEqual(row.is_salvage, 1)

        offered = {g["grade_name"] for g in get_grades()}
        self.assertNotIn("E", offered)
        self.assertNotIn("F", offered)
        self.assertIn("A", offered)
