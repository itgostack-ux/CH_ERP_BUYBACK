import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate, add_days, getdate

from buyback.exceptions import BuybackStatusError
from buyback.utils import log_audit, validate_indian_phone


class BuybackAssessment(Document):
    def before_insert(self):
        """Auto-assign sequential integer ID."""
        last = frappe.db.sql(
            "SELECT MAX(assessment_id) FROM `tabBuyback Assessment`"
        )[0][0] or 0
        self.assessment_id = last + 1

        self.status = "Draft"

        if not self.expires_on:
            validity_days = (
                frappe.db.get_single_value("Buyback Settings", "quote_validity_days") or 7
            )
            self.expires_on = add_days(nowdate(), validity_days)

    def before_submit(self):
        """Ensure status is Submitted when Frappe's standard submit is used."""
        if self.status == "Draft":
            self.status = "Submitted"
        if not self.quoted_price:
            self.quoted_price = self.estimated_price

    def validate(self):
        self._ensure_mobile_no()
        if self.mobile_no:
            self.mobile_no = validate_indian_phone(self.mobile_no, "Mobile No")
        self._update_customer_mobile()
        self._check_imei_blacklist()
        self._check_duplicate_active_assessment()
        self._resolve_customer_from_mobile()
        self._auto_fill_item_details()
        if self.diagnostic_tests:
            self._fill_diagnostic_impacts()
        if self.responses:
            self._fill_response_impacts()
        if self.diagnostic_tests or self.responses:
            self._calculate_estimate()
        # Default estimated_grade to "A" (best condition) if still unset
        if not self.estimated_grade:
            self.estimated_grade = frappe.db.get_value(
                "Grade Master", {"grade_name": "A"}, "name"
            )

    def before_save(self):
        if self.is_new():
            return
        old = self.get_doc_before_save()
        if old and round(float(old.quoted_price or 0), 2) != round(float(self.quoted_price or 0), 2):
            try:
                from ch_pos.audit import log_business_event
                log_business_event(
                    event_type="Buyback Value Edit",
                    ref_doctype="Buyback Assessment", ref_name=self.name,
                    before=f"₹{old.quoted_price}",
                    after=f"₹{self.quoted_price}",
                    remarks=f"Quoted price changed on assessment {self.name}",
                    company=self.get("company", ""),
                )
            except (ImportError, frappe.ValidationError):
                frappe.log_error(title=f"Audit log failed for buyback {self.name}")

    def _check_imei_blacklist(self):
        if self.imei_serial:
            from buyback.buyback.doctype.buyback_imei_blacklist.buyback_imei_blacklist import check_imei_and_block
            check_imei_and_block(self.imei_serial)

    def _ensure_mobile_no(self):
        """Fallback: pull mobile from Customer alternate/whatsapp if primary is empty."""
        if self.mobile_no or not self.customer:
            return
        cust = frappe.db.get_value(
            "Customer", self.customer,
            ["mobile_no", "ch_alternate_phone", "ch_whatsapp_number"],
            as_dict=True,
        )
        if cust:
            self.mobile_no = cust.mobile_no or cust.ch_alternate_phone or cust.ch_whatsapp_number

    def _update_customer_mobile(self):
        """Write mobile_no back to Customer if Customer has none."""
        if not self.mobile_no or not self.customer:
            return
        cust_mobile = frappe.db.get_value("Customer", self.customer, "mobile_no")
        if not cust_mobile:
            frappe.db.set_value("Customer", self.customer, "mobile_no", self.mobile_no)

    def _check_duplicate_active_assessment(self):
        """BB-1 fix: Prevent duplicate active buyback assessments for the same IMEI/serial."""
        if not self.imei_serial:
            return
        # Internal lifecycle transitions (e.g. mark_expired, cancel_assessment) must be able
        # to save even when another active assessment exists for the same IMEI.
        if self.flags.get("skip_duplicate_check"):
            return
        active_statuses = ("Draft", "Submitted", "In Progress", "Inspected", "Quoted")
        existing = frappe.db.get_value(
            "Buyback Assessment",
            {
                "imei_serial": self.imei_serial,
                "status": ("in", active_statuses),
                "name": ("!=", self.name or ""),
            },
            ["name", "status"],
            as_dict=True,
        )
        if existing:
            frappe.throw(
                _("An active buyback assessment ({0}, status: {1}) already exists for "
                  "IMEI/Serial {2}. Please complete or cancel it before creating a new one."
                ).format(existing.name, existing.status, self.imei_serial),
                title=_("Duplicate Assessment"),
            )

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
        """Look up price_impact_percent from Question Bank options for each response.
        BB-4 fix: Filter by applies_to_category if available on the assessment.
        """
        category = self.get("item_group") or self.get("category")
        for r in self.responses:
            # fetch_from runs after validate, so resolve manually
            if not r.question_code and r.question:
                r.question_code = frappe.db.get_value(
                    "Buyback Question Bank", r.question, "question_code"
                )
            if not r.question_code or not r.answer_value:
                continue
            # BB-4 fix: Prefer category-specific question, fall back to global
            filters = {"question_code": r.question_code, "disabled": 0}
            if category:
                qname = frappe.db.get_value(
                    "Buyback Question Bank",
                    {**filters, "applies_to_category": category},
                    "name",
                )
                if not qname:
                    qname = frappe.db.get_value(
                        "Buyback Question Bank",
                        {**filters, "applies_to_category": ["in", ["", None]]},
                        "name",
                    )
            else:
                qname = frappe.db.get_value("Buyback Question Bank", filters, "name")
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
        BB-4 fix: Filter by applies_to_category if available.
        """
        category = self.get("item_group") or self.get("category")
        for d in self.diagnostic_tests:
            # fetch_from runs after validate, so resolve manually
            if not d.test_code and d.test:
                d.test_code = frappe.db.get_value(
                    "Buyback Question Bank", d.test, "question_code"
                )
            if not d.test_code or not d.result:
                continue
            # BB-4 fix: Prefer category-specific question, fall back to global
            filters = {"question_code": d.test_code, "disabled": 0}
            if category:
                qname = frappe.db.get_value(
                    "Buyback Question Bank",
                    {**filters, "applies_to_category": category},
                    "name",
                )
                if not qname:
                    qname = frappe.db.get_value(
                        "Buyback Question Bank",
                        {**filters, "applies_to_category": ["in", ["", None]]},
                        "name",
                    )
            else:
                qname = frappe.db.get_value("Buyback Question Bank", filters, "name")
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
            )
            if not grade:
                frappe.log_error(f"Grade Master missing for grade '{grade_letter}'. Create A/B/C/D records in Grade Master.", "Buyback Grade Missing")
            self.estimated_grade = grade or None

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
        except (ValueError, KeyError, frappe.ValidationError, frappe.DoesNotExistError):
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

        Copies diagnostic tests and question responses into the new
        side-by-side inspection tables.  When source is "Store Manual"
        (POS in-store), the inspector columns are pre-filled with the
        same data so the store person doesn't have to re-enter answers.

        Returns the new Buyback Inspection doc.
        """
        if self.status not in ("Draft", "Submitted"):
            frappe.throw(
                _("Can only create inspection from a Draft or Submitted assessment."),
                exc=BuybackStatusError,
            )

        if not self.customer:
            frappe.throw(
                _("Customer is required before creating an inspection."),
                exc=BuybackStatusError,
            )

        is_pos = self.source == "Store Manual"

        inspection = frappe.new_doc("Buyback Inspection")
        inspection.buyback_assessment = self.name
        inspection.source = self.source
        inspection.customer = self.customer
        inspection.mobile_no = self.mobile_no
        inspection.store = self.store
        inspection.company = self.company
        inspection.item = self.item
        inspection.item_name = self.item_name
        inspection.item_group = self.item_group
        inspection.brand = self.brand
        inspection.imei_serial = self.imei_serial
        inspection.device_age_months = self.device_age_months
        inspection.warranty_status = self.warranty_status
        inspection.quoted_price = self.quoted_price or self.estimated_price
        inspection.estimated_grade = self.estimated_grade
        inspection.estimated_price = self.estimated_price
        inspection.pre_inspection_grade = self.estimated_grade
        inspection.checklist_template = checklist_template

        if self.source in ("Mobile App", "In-Store Kiosk"):
            inspection.diagnostic_source = "Mobile App"
        else:
            inspection.diagnostic_source = "In-Store"

        # Copy diagnostic tests → inspection_diagnostics
        for d in (self.diagnostic_tests or []):
            row = {
                "test": d.test,
                "test_code": d.test_code,
                "test_name": d.test_name,
                "assessment_result": d.result,
                "assessment_depreciation": d.depreciation_percent,
            }
            if is_pos:
                row["inspector_result"] = d.result
                row["inspector_depreciation"] = d.depreciation_percent
            inspection.append("inspection_diagnostics", row)

        # Copy responses → inspection_responses
        for r in (self.responses or []):
            row = {
                "question": r.question,
                "question_code": r.question_code,
                "question_text": r.question_text,
                "assessment_answer": r.answer_value,
                "assessment_answer_label": r.answer_label,
                "assessment_impact": r.price_impact_percent,
            }
            if is_pos:
                row["inspector_answer"] = r.answer_value
                row["inspector_answer_label"] = r.answer_label
                row["inspector_impact"] = r.price_impact_percent
            inspection.append("inspection_responses", row)

        # For POS, also pre-fill the post-inspection grade
        if is_pos:
            inspection.post_inspection_grade = self.estimated_grade
            inspection.revised_price = self.quoted_price or self.estimated_price

        inspection.insert(ignore_permissions=True)

        if checklist_template:
            inspection.populate_checklist()
            inspection.flags.ignore_mandatory = True
            inspection.save(ignore_permissions=True)
            inspection.flags.ignore_mandatory = False

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
            # Lifecycle close-out: bypass duplicate-active guard so the very record we are
            # expiring (which IS the duplicate-blocker) can be saved cleanly.
            self.flags.skip_duplicate_check = True
            self.save()
            log_audit("Assessment Expired", "Buyback Assessment", self.name)

    def cancel_assessment(self):
        """Manually cancel."""
        if self.status in ("Expired", "Cancelled"):
            return
        self.status = "Cancelled"
        self.flags.skip_duplicate_check = True
        self.save()
        log_audit("Assessment Cancelled", "Buyback Assessment", self.name)

    def is_valid(self):
        """Check if assessment is still within validity period."""
        if self.status not in ("Draft", "Submitted"):
            return False
        if self.expires_on and getdate(self.expires_on) < getdate(nowdate()):
            return False
        return True
