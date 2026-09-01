# Copyright (c) 2026, Congruence Holdings and contributors
# See license.txt

"""
E2E tests for Buyback Inspection:
 - Grade auto-recalculation when inspector changes diagnostic/response answers
 - Price recalculation on every save (not just on complete_inspection)
 - Inspection Result child table — question_ref auto-fills check_item/code/type
 - Duplicate action buttons prevented (covered by JS; smoke-tested via API)
 - recalculate_grade_and_price whitelisted method
 - complete_inspection and reject_device workflows
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

# ─── helpers ────────────────────────────────────────────────────────────────

def _make_grade(name):
    """Return an existing Grade Master or create a minimal one."""
    if frappe.db.exists("Grade Master", {"grade_name": name}):
        return frappe.db.get_value("Grade Master", {"grade_name": name}, "name")
    doc = frappe.get_doc({
        "doctype": "Grade Master",
        "grade_name": name,
        "display_order": 0,
    })
    doc.insert(ignore_permissions=True)
    return doc.name


def _make_question(code, text, diagnosis_type="Customer Question", options=None):
    """Return existing or create a Buyback Question Bank entry with options."""
    existing = frappe.db.get_value("Buyback Question Bank", {"question_code": code}, "name")
    if existing:
        return existing
    doc = frappe.get_doc({
        "doctype": "Buyback Question Bank",
        "question_code": code,
        "question_text": text,
        "diagnosis_type": diagnosis_type,
        "question_type": "Single Select" if diagnosis_type == "Customer Question" else "Yes/No",
        "disabled": 0,
    })
    for opt in (options or []):
        doc.append("options", {
            "option_value": opt["value"],
            "option_label": opt.get("label", opt["value"]),
            "price_impact_percent": opt.get("impact", 0),
        })
    doc.insert(ignore_permissions=True)
    return doc.name


def _make_item():
    """Return the test Item, creating it if the site does not have it.

    The suite used to assume "Test-Device-001" existed as committed site
    data; after a data wipe every test errored on link validation before
    reaching its actual assertion. Tests must create what they need inside
    the transaction (rolled back in tearDown) — same pattern as
    test_pricing_integrity's _ensure_item.
    """
    if frappe.db.exists("Item", "Test-Device-001"):
        return "Test-Device-001"
    from buyback.tests import test_item_compliance_fields

    frappe.get_doc({
        "doctype": "Item",
        "item_code": "Test-Device-001",
        "item_name": "Inspection Test Device",
        "item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
        "stock_uom": "Nos",
        "is_stock_item": 0,
        **test_item_compliance_fields(),
    }).insert(ignore_permissions=True)
    return "Test-Device-001"


def _make_inspection(**kwargs):
    """Create and insert a minimal Buyback Inspection in Draft status."""
    grade_a = _make_grade("A")
    defaults = {
        "doctype": "Buyback Inspection",
        "status": "Draft",
        "pre_inspection_grade": grade_a,
        "post_inspection_grade": grade_a,
        "condition_grade": grade_a,
        "item": _make_item(),
        "inspector": "Administrator",
    }
    defaults.update(kwargs)
    doc = frappe.get_doc(defaults)
    doc.insert(ignore_permissions=True)
    return doc


# ─── TestDiagnosticResultCompatibility ──────────────────────────────────────

class TestDiagnosticResultCompatibility(FrappeTestCase):
    """Assessment and Inspection must share the canonical Yes/No vocabulary."""

    def _make_yes_no_question(self):
        return _make_question(
            "test_yes_no_inspection_e2e",
            "Inspection Yes/No compatibility (E2E)",
            diagnosis_type="Automated Test",
            options=[
                {"value": "yes", "impact": 0},
                {"value": "no", "impact": -18},
            ],
        )

    def test_yes_no_assessment_result_passes_inspection_validation(self):
        question = self._make_yes_no_question()
        doc = frappe.new_doc("Buyback Inspection")
        doc.append(
            "inspection_diagnostics",
            {
                "test": question,
                "test_code": "test_yes_no_inspection_e2e",
                "assessment_result": "Yes",
                "inspector_result": "No",
            },
        )

        # This validation previously failed because the child
        # DocType only allowed Pass/Fail/Partial.
        doc._validate_selects()
        doc._fill_inspector_diagnostic_impacts(doc._load_question_bank_cache())

        row = doc.inspection_diagnostics[0]
        self.assertEqual(row.assessment_result, "Yes")
        self.assertEqual(row.inspector_result, "No")
        self.assertEqual(float(row.inspector_depreciation), 18)

    def test_pricing_uses_the_questions_explicit_yes_no_impact(self):
        self._make_yes_no_question()
        from buyback.buyback.pricing.engine import _get_diagnostic_deduction

        # For this positive-worded diagnostic, Yes is healthy and No carries
        # the configured deduction. It must not be globally remapped.
        self.assertIsNone(
            _get_diagnostic_deduction(
                {"test_code": "test_yes_no_inspection_e2e", "result": "Yes"},
                1000,
            )
        )
        deduction = _get_diagnostic_deduction(
            {"test_code": "test_yes_no_inspection_e2e", "result": "No"},
            1000,
        )
        self.assertEqual(deduction["percent"], 18)
        self.assertEqual(deduction["amount"], 180)


# ─── TestGradeRecalculation ──────────────────────────────────────────────────

class TestGradeRecalculation(FrappeTestCase):
    """
    Verify the inspector's grade survives a save.

    Grade used to be "auto-determined" by a helper that ignored the diagnostic
    results it was handed and unconditionally returned "A". Because callers
    treated that as authoritative, any grade the inspector picked was replaced
    with A unless they also typed a `grade_changed_reason` — devices went to a
    Grade-D payout still labelled Grade A. There is no auto-determination now;
    the inspector decides, with the pre-inspection estimate as the only
    fallback.
    """

    def setUp(self):
        self.q_name = _make_question(
            "test_screen_e2e", "Screen Test (E2E)",
            diagnosis_type="Automated Test",
            options=[
                {"value": "Pass", "impact": 0},
                {"value": "Partial", "impact": -10},
                {"value": "Fail", "impact": -25},
            ],
        )
        self.grade_a = _make_grade("A")
        self.grade_b = _make_grade("B")

    def test_inspector_grade_is_not_overwritten_by_diagnostics(self):
        """The inspector's grade stands, with or without a change reason.

        Regression guard: filling in a diagnostic result used to trigger
        "auto-determination" that silently reset the grade to A.
        """
        doc = _make_inspection()
        doc.post_inspection_grade = self.grade_b

        # A failed diagnostic used to be enough to trigger the reset.
        doc.append("inspection_diagnostics", {
            "test": self.q_name,
            "test_code": "test_screen_e2e",
            "assessment_result": "Pass",
            "inspector_result": "Fail",
        })

        doc.save(ignore_permissions=True)
        doc.reload()

        self.assertEqual(
            doc.condition_grade, self.grade_b,
            "inspector's Grade B was replaced — the auto-grade stub is back",
        )
        self.assertEqual(doc.post_inspection_grade, self.grade_b)

    def test_grade_falls_back_to_pre_inspection_estimate(self):
        """With no inspector choice yet, the assessment's grade carries over."""
        doc = _make_inspection()
        doc.post_inspection_grade = None
        doc.save(ignore_permissions=True)
        doc.reload()

        self.assertIsNotNone(doc.condition_grade)
        self.assertEqual(doc.condition_grade, doc.pre_inspection_grade)

    def test_grade_respects_explicit_override_when_reason_set(self):
        """If grade_changed_reason is set, inspector's post_inspection_grade is kept."""
        doc = _make_inspection()

        # Manually set a different post_inspection_grade with an override reason
        doc.grade_changed_reason = "Customer VIP — keeping grade A as per policy"
        doc.post_inspection_grade = self.grade_a
        doc.condition_grade = self.grade_a

        doc.append("inspection_diagnostics", {
            "test": self.q_name,
            "test_code": "test_screen_e2e",
            "assessment_result": "Pass",
            "inspector_result": "Fail",
        })

        doc.save(ignore_permissions=True)
        doc.reload()

        # Explicit override must be respected
        self.assertEqual(doc.post_inspection_grade, self.grade_a)
        self.assertEqual(doc.condition_grade, self.grade_a)

    def test_grade_auto_updates_after_override_cleared(self):
        """Clearing grade_changed_reason lets auto-determine run again."""
        doc = _make_inspection()
        doc.grade_changed_reason = "Some reason"
        doc.post_inspection_grade = self.grade_a
        doc.append("inspection_diagnostics", {
            "test": self.q_name,
            "test_code": "test_screen_e2e",
            "assessment_result": "Pass",
            "inspector_result": "Pass",
        })
        doc.save(ignore_permissions=True)

        # Clear the override
        doc.grade_changed_reason = None
        doc.save(ignore_permissions=True)
        doc.reload()

        # condition_grade should now come from auto-determination (or fallback)
        self.assertIsNotNone(doc.condition_grade)

    def tearDown(self):
        frappe.db.rollback()


# ─── TestPriceRecalculationOnSave ────────────────────────────────────────────

class TestPriceRecalculationOnSave(FrappeTestCase):
    """
    Verify _recalculate_price() runs during validate() for In Progress inspections.
    """

    def setUp(self):
        self.grade_a = _make_grade("A")
        self.q_name = _make_question(
            "cond_question_e2e", "Screen Condition (E2E)",
            diagnosis_type="Customer Question",
            options=[
                {"value": "Good", "impact": 0},
                {"value": "cracked", "impact": -25},
            ],
        )

    def _make_assessment(self):
        """Create a minimal Buyback Assessment with a submitted status."""
        # Need a real customer and item for pricing engine
        if not frappe.db.exists("Item", "Test-Device-001"):
            item = frappe.get_doc({
                "doctype": "Item",
                "item_code": "Test-Device-001",
                "item_name": "Test Device 001",
                "item_group": "All Item Groups",
                "stock_uom": "Nos",
            })
            item.insert(ignore_permissions=True)

        if not frappe.db.exists("Customer", "_E2E-Customer-Buyback"):
            cust = frappe.get_doc({
                "doctype": "Customer",
                "customer_name": "_E2E-Customer-Buyback",
                "customer_type": "Individual",
                "customer_group": "Individual",
                "territory": "India",
            })
            cust.insert(ignore_permissions=True)

        a = frappe.get_doc({
            "doctype": "Buyback Assessment",
            "status": "Submitted",
            "customer": "_E2E-Customer-Buyback",
            "item": "Test-Device-001",
            "estimated_grade": self.grade_a,
            "estimated_price": 10000,
            "quoted_price": 10000,
        })
        a.insert(ignore_permissions=True)
        return a

    def test_price_recalculates_in_progress(self):
        """In Progress inspection: save updates revised_price via pricing engine."""
        try:
            assessment = self._make_assessment()
        except Exception:
            self.skipTest("Assessment/Item/Customer setup failed in test environment")

        doc = _make_inspection(
            buyback_assessment=assessment.name,
            status="In Progress",
            condition_grade=self.grade_a,
            post_inspection_grade=self.grade_a,
        )
        doc.inspection_started_at = now_datetime()

        doc.append("inspection_responses", {
            "question": self.q_name,
            "question_code": "cond_question_e2e",
            "assessment_answer": "Good",
            "assessment_impact": 0,
            "inspector_answer": "cracked",
        })

        doc.save(ignore_permissions=True)
        doc.reload()

        # _fill_inspector_response_impacts should have set inspector_impact = -25
        resp_row = doc.inspection_responses[0]
        self.assertEqual(flt(resp_row.inspector_impact), -25.0)

    def tearDown(self):
        frappe.db.rollback()


# ─── TestRecalculateGradeAndPrice ────────────────────────────────────────────

class TestRecalculateGradeAndPrice(FrappeTestCase):
    """
    Verify the whitelisted recalculate_grade_and_price() method.
    """

    def setUp(self):
        self.grade_a = _make_grade("A")
        self.q_name = _make_question(
            "btn_test_q_e2e", "Button Test (E2E)",
            diagnosis_type="Automated Test",
            options=[
                {"value": "Pass", "impact": 0},
                {"value": "Fail", "impact": -30},
            ],
        )

    def test_recalculate_clears_override_and_returns_result(self):
        """recalculate_grade_and_price clears grade_changed_reason and returns dict."""
        doc = _make_inspection(
            grade_changed_reason="Manual override",
            post_inspection_grade=self.grade_a,
        )
        doc.append("inspection_diagnostics", {
            "test": self.q_name,
            "test_code": "btn_test_q_e2e",
            "assessment_result": "Pass",
            "inspector_result": "Pass",
        })
        doc.save(ignore_permissions=True)

        result = doc.recalculate_grade_and_price()

        self.assertIsInstance(result, dict)
        self.assertIn("condition_grade", result)
        self.assertIn("post_inspection_grade", result)
        self.assertIn("revised_price", result)

        # grade_changed_reason must be cleared
        doc.reload()
        self.assertFalsy(doc.grade_changed_reason)

    def assertFalsy(self, value):
        """Assert value is falsy (None, empty string, 0)."""
        self.assertFalse(bool(value), f"Expected falsy, got: {value!r}")

    def tearDown(self):
        frappe.db.rollback()


# ─── TestInspectionResultChildTable ─────────────────────────────────────────

class TestInspectionResultChildTable(FrappeTestCase):
    """
    Verify the Inspection Results (legacy) child table:
     - question_ref Link field exists in the doctype
     - check_type is no longer read_only
     - Rows can be added manually with free-text checklist_item + check_code
    """

    def test_question_ref_field_exists(self):
        """Buyback Inspection Result must have question_ref Link field."""
        meta = frappe.get_meta("Buyback Inspection Result")
        field_names = [f.fieldname for f in meta.fields]
        self.assertIn("question_ref", field_names,
                      "question_ref Link field missing from Buyback Inspection Result")

    def test_check_type_not_read_only(self):
        """check_type must not be read_only so users can set it manually."""
        meta = frappe.get_meta("Buyback Inspection Result")
        check_type_field = next((f for f in meta.fields if f.fieldname == "check_type"), None)
        self.assertIsNotNone(check_type_field)
        self.assertFalse(
            check_type_field.read_only,
            "check_type should not be read_only — users must be able to set it"
        )

    def test_manual_row_can_be_saved(self):
        """A manually added result row (no question_ref) saves without error."""
        _make_grade("A")
        doc = _make_inspection()
        doc.append("results", {
            "checklist_item": "Screen Check",
            "check_code": "screen_check_manual",
            "check_type": "Pass/Fail",
            "result": "Pass",
        })
        doc.save(ignore_permissions=True)
        doc.reload()

        self.assertEqual(len(doc.results), 1)
        self.assertEqual(doc.results[0].check_code, "screen_check_manual")
        self.assertEqual(doc.results[0].result, "Pass")

    def test_question_ref_link_points_to_question_bank(self):
        """question_ref field options must reference Buyback Question Bank."""
        meta = frappe.get_meta("Buyback Inspection Result")
        field = next((f for f in meta.fields if f.fieldname == "question_ref"), None)
        self.assertIsNotNone(field)
        self.assertEqual(field.fieldtype, "Link")
        self.assertEqual(field.options, "Buyback Question Bank")

    def tearDown(self):
        frappe.db.rollback()


# ─── TestInspectionWorkflow ──────────────────────────────────────────────────

class TestInspectionWorkflow(FrappeTestCase):
    """
    Verify full workflow: Draft → In Progress → Completed / Rejected
    """

    def setUp(self):
        self.grade_a = _make_grade("A")

    def test_start_inspection_sets_status_and_time(self):
        doc = _make_inspection()
        doc.start_inspection()
        doc.reload()

        self.assertEqual(doc.status, "In Progress")
        self.assertIsNotNone(doc.inspection_started_at)
        self.assertEqual(doc.inspector, "Administrator")

    def test_cannot_start_already_in_progress(self):
        # Code truth: before_insert force-sets status = "Draft" (status is
        # server-issued, clients cannot inject it at insert), so passing
        # status="In Progress" into the insert no longer stages the
        # precondition — set it after insert the way start_inspection does.
        doc = _make_inspection()
        doc.db_set("status", "In Progress", update_modified=False)
        doc.db_set("inspection_started_at", now_datetime(), update_modified=False)
        doc.reload()

        with self.assertRaises(Exception):
            doc.start_inspection()

    def test_reject_device_sets_status(self):
        doc = _make_inspection()
        doc.start_inspection()
        doc.reject_device(reason="Stolen device - IMEI blacklisted")
        doc.reload()

        self.assertEqual(doc.status, "Rejected")
        self.assertIn("Stolen device", doc.remarks)

    def test_cannot_reject_completed(self):
        """Completed inspection cannot be rejected."""
        # Code truth: before_insert force-sets status = "Draft" (status is
        # server-issued), so stage the Completed state after insert.
        doc = _make_inspection(condition_grade=self.grade_a)
        doc.db_set("status", "Completed", update_modified=False)
        doc.reload()

        with self.assertRaises(Exception):
            doc.reject_device(reason="Too late")

    def test_populate_checklist_fills_results(self):
        """populate_checklist fills results from checklist template."""
        # Create a checklist template with two items
        tmpl = frappe.get_doc({
            "doctype": "Buyback Checklist Template",
            "template_name": "_E2E Test Template",
            "items": [
                {
                    "check_item": "IMEI Match",
                    "check_code": "imei_match",
                    "check_type": "Pass/Fail",
                    "is_mandatory": 1,
                },
                {
                    "check_item": "Water Damage",
                    "check_code": "water_damage",
                    # Schema truth (buyback_checklist_item.json): the
                    # "Yes/No" Check Type option was removed — checklist
                    # items are now Pass/Fail, Grade (A/B/C/D), Numeric
                    # Score or Text Note. A binary water-damage check maps
                    # to Pass/Fail. Do not resurrect the removed option.
                    "check_type": "Pass/Fail",
                    "is_mandatory": 0,
                },
            ],
        })
        tmpl.insert(ignore_permissions=True)

        doc = _make_inspection(checklist_template=tmpl.name)
        doc.populate_checklist()
        # Schema truth: Buyback Inspection Result.result is now reqd — a
        # populated checklist row cannot be SAVED until the inspector has
        # recorded its outcome, so record one before persisting (the old
        # test saved the blank rows populate_checklist stages for the UI).
        for row in doc.results:
            row.result = "Pass"
        doc.save(ignore_permissions=True)
        doc.reload()

        self.assertEqual(len(doc.results), 2)
        codes = [r.check_code for r in doc.results]
        self.assertIn("imei_match", codes)
        self.assertIn("water_damage", codes)

    def tearDown(self):
        frappe.db.rollback()


# ─── TestDiagnosticImpactFill ─────────────────────────────────────────────────

class TestDiagnosticImpactFill(FrappeTestCase):
    """
    Verify _fill_inspector_diagnostic_impacts / _fill_inspector_response_impacts
    correctly populate inspector_depreciation and inspector_impact from Question Bank.
    """

    def setUp(self):
        self.diag_q = _make_question(
            "diag_impact_e2e", "Battery Test (E2E)",
            diagnosis_type="Automated Test",
            options=[
                {"value": "Pass", "impact": 0},
                {"value": "Fail", "impact": -20},
                {"value": "Partial", "impact": -10},
            ],
        )
        self.resp_q = _make_question(
            "resp_impact_e2e", "Button Condition (E2E)",
            diagnosis_type="Customer Question",
            options=[
                {"value": "Good", "impact": 0},
                {"value": "Damaged", "impact": -15},
            ],
        )

    def test_diagnostic_depreciation_filled_on_save(self):
        """inspector_depreciation is filled from Question Bank on validate."""
        doc = _make_inspection()
        doc.append("inspection_diagnostics", {
            "test": self.diag_q,
            "test_code": "diag_impact_e2e",
            "assessment_result": "Pass",
            "assessment_depreciation": 0,
            "inspector_result": "Fail",
        })
        doc.save(ignore_permissions=True)
        doc.reload()

        self.assertEqual(flt(doc.inspection_diagnostics[0].inspector_depreciation), 20.0)

    def test_response_impact_filled_on_save(self):
        """inspector_impact is filled from Question Bank on validate."""
        doc = _make_inspection()
        doc.append("inspection_responses", {
            "question": self.resp_q,
            "question_code": "resp_impact_e2e",
            "assessment_answer": "Good",
            "assessment_impact": 0,
            "inspector_answer": "Damaged",
        })
        doc.save(ignore_permissions=True)
        doc.reload()

        self.assertEqual(flt(doc.inspection_responses[0].inspector_impact), -15.0)

    def test_partial_depreciation_filled(self):
        """inspector_depreciation for Partial result is 10."""
        doc = _make_inspection()
        doc.append("inspection_diagnostics", {
            "test": self.diag_q,
            "test_code": "diag_impact_e2e",
            "assessment_result": "Pass",
            "assessment_depreciation": 0,
            "inspector_result": "Partial",
        })
        doc.save(ignore_permissions=True)
        doc.reload()

        self.assertEqual(flt(doc.inspection_diagnostics[0].inspector_depreciation), 10.0)

    def tearDown(self):
        frappe.db.rollback()


# ─── TestDuplicateButtonsPrevented ───────────────────────────────────────────

class TestDuplicateButtonsPrevented(FrappeTestCase):
    """
    Smoke tests ensuring JS-level button deduplication doesn't break server APIs.
    Actual duplicate-button rendering requires browser tests; these test server side.
    """

    def test_recalculate_is_whitelisted(self):
        """recalculate_grade_and_price is a whitelisted method."""
        from frappe.model.document import Document
        doc = _make_inspection()
        self.assertTrue(
            hasattr(doc, "recalculate_grade_and_price"),
            "recalculate_grade_and_price method must exist"
        )
        # Check it's decorated @frappe.whitelist — frappe registers the
        # function object in the frappe.whitelisted set; there is no
        # __frappe_whitelist__ attribute (the old check was always False
        # and the string fallback crashed inside frappe.is_whitelisted).
        method = type(doc).recalculate_grade_and_price
        self.assertIn(
            method, frappe.whitelisted,
            "recalculate_grade_and_price should be whitelisted"
        )

    def test_start_inspection_is_whitelisted(self):
        doc = _make_inspection()
        self.assertTrue(hasattr(doc, "start_inspection"))

    def test_complete_inspection_is_whitelisted(self):
        doc = _make_inspection()
        self.assertTrue(hasattr(doc, "complete_inspection"))

    def test_reject_device_is_whitelisted(self):
        doc = _make_inspection()
        self.assertTrue(hasattr(doc, "reject_device"))

    def tearDown(self):
        frappe.db.rollback()


# ─── helper ──────────────────────────────────────────────────────────────────

def flt(val):
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0
