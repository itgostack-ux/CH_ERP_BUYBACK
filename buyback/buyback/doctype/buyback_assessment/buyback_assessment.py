import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate, add_days, getdate

from buyback.exceptions import BuybackStatusError
from buyback.utils import log_audit


class BuybackAssessment(Document):
    def before_insert(self):
        """Auto-assign sequential integer ID using advisory lock."""
        frappe.db.sql("SELECT GET_LOCK('buyback_assessment_id', 10)")
        try:
            last = frappe.db.sql(
                "SELECT MAX(assessment_id) FROM `tabBuyback Assessment`"
            )[0][0] or 0
            self.assessment_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('buyback_assessment_id')")

        self.status = "Draft"

        if not self.expires_on:
            validity_days = (
                frappe.db.get_single_value("Buyback Settings", "quote_validity_days") or 7
            )
            self.expires_on = add_days(nowdate(), validity_days)

    def validate(self):
        self._check_imei_blacklist()
        self._resolve_customer_from_mobile()
        self._auto_fill_item_details()
        if self.diagnostic_tests:
            self._fill_diagnostic_impacts()
        if self.responses:
            self._fill_response_impacts()
        if self.diagnostic_tests or self.responses:
            self._calculate_estimate()

    def _check_imei_blacklist(self):
        if self.imei_serial:
            from buyback.buyback.doctype.buyback_imei_blacklist.buyback_imei_blacklist import check_imei_and_block
            check_imei_and_block(self.imei_serial)

    # ------------------------------------------------------------------
    # Business helpers
    # ------------------------------------------------------------------

    def _resolve_customer_from_mobile(self):
        """Link Customer record from mobile_no if not already set."""
        if self.customer or not self.mobile_no:
            return
        cust = frappe.db.get_value(
            "Customer", {"mobile_no": self.mobile_no}, "name"
        )
        if cust:
            self.customer = cust

    def _auto_fill_item_details(self):
        """Auto-fill brand and item_group from Item."""
        if not self.item:
            return
        item = frappe.db.get_value(
            "Item", self.item, ["brand", "item_group", "item_name"], as_dict=True
        )
        if item:
            if not self.brand:
                self.brand = item.brand
            if not self.item_group:
                self.item_group = item.item_group
            if not self.item_name:
                self.item_name = item.item_name

    def _fill_response_impacts(self):
        """Look up price_impact_percent from Question Bank options for each response."""
        for r in self.responses:
            # fetch_from runs after validate, so resolve manually
            if not r.question_code and r.question:
                r.question_code = frappe.db.get_value(
                    "Buyback Question Bank", r.question, "question_code"
                )
            if not r.question_code or not r.answer_value:
                continue
            qname = frappe.db.get_value(
                "Buyback Question Bank",
                {"question_code": r.question_code, "disabled": 0},
                "name",
            )
            if not qname:
                continue
            impact = frappe.db.get_value(
                "Buyback Question Option",
                {"parent": qname, "option_value": r.answer_value},
                "price_impact_percent",
            )
            if impact is not None:
                r.price_impact_percent = impact

    def _fill_diagnostic_impacts(self):
        """Look up depreciation_percent from Question Bank options for each diagnostic test.

        Automated tests store results as Pass/Fail/Partial which map to
        option_value in the Question Bank options table.
        """
        for d in self.diagnostic_tests:
            # fetch_from runs after validate, so resolve manually
            if not d.test_code and d.test:
                d.test_code = frappe.db.get_value(
                    "Buyback Question Bank", d.test, "question_code"
                )
            if not d.test_code or not d.result:
                continue
            qname = frappe.db.get_value(
                "Buyback Question Bank",
                {"question_code": d.test_code, "disabled": 0},
                "name",
            )
            if not qname:
                continue
            impact = frappe.db.get_value(
                "Buyback Question Option",
                {"parent": qname, "option_value": d.result},
                "price_impact_percent",
            )
            if impact is not None:
                d.depreciation_percent = abs(impact)

    def _calculate_estimate(self):
        """Run the pricing engine against customer responses to get estimated price."""
        try:
            from buyback.buyback.pricing.engine import calculate_estimated_price
            from buyback.api import _auto_determine_grade

            # Auto-determine grade from diagnostic test results
            diagnostic_data = []
            for d in (self.diagnostic_tests or []):
                diagnostic_data.append({
                    "test": d.test,
                    "test_code": d.test_code,
                    "result": d.result,
                    "depreciation_percent": d.depreciation_percent,
                })

            grade_letter = _auto_determine_grade(diagnostic_data)
            grade = frappe.db.get_value(
                "Grade Master", {"grade_name": grade_letter}, "name"
            ) or "GRD-00001"
            self.estimated_grade = grade

            responses_data = []
            for r in (self.responses or []):
                responses_data.append({
                    "question": r.question,
                    "question_code": r.question_code,
                    "answer_value": r.answer_value,
                    "answer_label": r.answer_label,
                    "price_impact_percent": r.price_impact_percent,
                })

            result = calculate_estimated_price(
                item_code=self.item,
                grade=self.estimated_grade,
                warranty_status=self.warranty_status,
                device_age_months=self.device_age_months,
                responses=responses_data,
                diagnostic_tests=diagnostic_data,
                brand=self.brand,
                item_group=self.item_group,
            )

            self.estimated_price = result.get("estimated_price", 0)
        except Exception:
            frappe.log_error(title=f"Assessment pricing failed: {self.name}")

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    def submit_assessment(self):
        """Customer finalises the self-assessment."""
        if self.status != "Draft":
            frappe.throw(
                _("Can only submit a Draft assessment."),
                exc=BuybackStatusError,
            )
        if not self.responses and not self.diagnostic_tests:
            frappe.throw(
                _("At least one diagnostic test or customer response is required."),
                exc=BuybackStatusError,
            )
        # Default quoted_price to estimated_price if not manually set
        if not self.quoted_price:
            self.quoted_price = self.estimated_price
        self.status = "Submitted"
        self.save()
        log_audit("Assessment Submitted", "Buyback Assessment", self.name)

    def create_inspection(self, checklist_template=None):
        """Create a Buyback Inspection directly from this assessment.

        Returns the new Buyback Inspection doc.
        """
        if self.status != "Submitted":
            frappe.throw(
                _("Can only create inspection from a Submitted assessment."),
                exc=BuybackStatusError,
            )

        if not self.customer:
            frappe.throw(
                _("Customer is required before creating an inspection."),
                exc=BuybackStatusError,
            )

        inspection = frappe.new_doc("Buyback Inspection")
        inspection.buyback_assessment = self.name
        inspection.customer = self.customer
        inspection.mobile_no = self.mobile_no
        inspection.store = self.store
        inspection.company = self.company
        inspection.item = self.item
        inspection.item_name = self.item_name
        inspection.imei_serial = self.imei_serial
        inspection.quoted_price = self.quoted_price or self.estimated_price
        inspection.pre_inspection_grade = self.estimated_grade
        inspection.checklist_template = checklist_template

        if self.source in ("Mobile App", "In-Store Kiosk"):
            inspection.diagnostic_source = "Mobile App"
        else:
            inspection.diagnostic_source = "In-Store"

        inspection.insert(ignore_permissions=True)

        if checklist_template:
            inspection.populate_checklist()
            inspection.save()

        # Update self
        self.buyback_inspection = inspection.name
        self.status = "Inspection Created"
        self.save()

        log_audit(
            "Inspection Created from Assessment",
            "Buyback Assessment", self.name,
            new_value={"inspection": inspection.name},
        )
        return inspection

    def mark_expired(self):
        """Auto-expire assessment."""
        if self.status in ("Draft", "Submitted"):
            self.status = "Expired"
            self.save()
            log_audit("Assessment Expired", "Buyback Assessment", self.name)

    def cancel_assessment(self):
        """Manually cancel."""
        if self.status in ("Expired", "Cancelled"):
            return
        self.status = "Cancelled"
        self.save()
        log_audit("Assessment Cancelled", "Buyback Assessment", self.name)

    def is_valid(self):
        """Check if assessment is still within validity period."""
        if self.status not in ("Draft", "Submitted"):
            return False
        if self.expires_on and getdate(self.expires_on) < getdate(nowdate()):
            return False
        return True
