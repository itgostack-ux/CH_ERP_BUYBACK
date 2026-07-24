import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate, add_days, getdate, now_datetime

from buyback.exceptions import BuybackStatusError
from buyback.utils import (
    log_audit,
    next_numeric_external_id,
    require_scoped_document_action,
    sync_customer_identity,
    update_customer_mobile_if_missing,
    validate_indian_phone,
)


class BuybackAssessment(Document):
    def before_insert(self):
        self.assessment_id = next_numeric_external_id(
            "Buyback Assessment", "assessment_id"
        )

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
        update_customer_mobile_if_missing(self.customer, self.mobile_no)
        self._check_imei_blacklist()
        self._check_duplicate_active_assessment()
        self._resolve_customer_from_mobile()
        sync_customer_identity(self)
        self._auto_fill_item_details()
        impact_cache = (
            self._load_question_impact_cache()
            if self.diagnostic_tests or self.responses else None
        )
        if self.diagnostic_tests:
            self._fill_diagnostic_impacts(impact_cache)
        if self.responses:
            self._fill_response_impacts(impact_cache)
        if self.item:
            # Price whenever the item is known — the base grade/warranty/age
            # price must populate even with no responses or diagnostic tests.
            self._calculate_estimate()
        # P2-10: Block submission with unanswered diagnostic responses so the
        # inspector cannot grade a device with a partial question bank.

        if not self.get("is_phone_dead"):
            if self.responses and self.status in ("Submitted", "Inspected", "Quoted"):
                unanswered = [r for r in self.responses if not (r.get("answer") or "").strip()]
                if unanswered:
                    missing = ", ".join(
                        (r.get("question_text") or r.get("question_code") or r.get("name") or "")
                        for r in unanswered[:5]
                    )
                    frappe.throw(
                        _("All diagnostic questions must be answered before submission. "
                        "Unanswered: {0}").format(missing),
                        title=_("Incomplete Question Bank"),
                    )
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

    def _load_question_impact_cache(self) -> dict:
        """Resolve all response/diagnostic question options in bounded queries."""
        response_rows = list(self.responses or [])
        diagnostic_rows = list(self.diagnostic_tests or [])
        linked_names = {
            row.question
            for row in response_rows
            if row.question and not row.question_code
        } | {
            row.test
            for row in diagnostic_rows
            if row.test and not row.test_code
        }
        code_by_name = {}
        if linked_names:
            code_by_name = {
                row.name: row.question_code
                for row in frappe.get_all(
                    "Buyback Question Bank",
                    filters={"name": ["in", sorted(linked_names)]},
                    fields=["name", "question_code"],
                )
            }
        for row in response_rows:
            if not row.question_code and row.question:
                row.question_code = code_by_name.get(row.question)
        for row in diagnostic_rows:
            if not row.test_code and row.test:
                row.test_code = code_by_name.get(row.test)

        codes = sorted({
            code
            for code in (
                [row.question_code for row in response_rows]
                + [row.test_code for row in diagnostic_rows]
            )
            if code
        })
        if not codes:
            return {"question_by_code": {}, "impacts": {}}

        questions = frappe.get_all(
            "Buyback Question Bank",
            filters={"question_code": ["in", codes], "disabled": 0},
            fields=[
                "name", "question_code", "display_order", "question_id",
                "applies_to_category",
            ],
            order_by="question_code asc, display_order asc, question_id asc",
        )
        if not questions:
            return {"question_by_code": {}, "impacts": {}}

        categories: dict[str, set[str]] = {row.name: set() for row in questions}
        for row in frappe.get_all(
            "Buyback Question Applicable Category",
            filters={
                "parent": ["in", [row.name for row in questions]],
                "parenttype": "Buyback Question Bank",
                "parentfield": "applies_to_categories",
            },
            fields=["parent", "item_group"],
        ):
            if row.item_group:
                categories.setdefault(row.parent, set()).add(row.item_group)

        category = self.get("item_group") or self.get("category")
        candidates_by_code: dict[str, list] = {}
        for row in questions:
            candidates_by_code.setdefault(row.question_code, []).append(row)
        question_by_code = {}
        for code, candidates in candidates_by_code.items():
            selected = None
            global_question = None
            for candidate in candidates:
                applicable = categories.get(candidate.name) or (
                    {candidate.applies_to_category}
                    if candidate.applies_to_category else set()
                )
                if category and category in applicable and selected is None:
                    selected = candidate.name
                if not applicable and global_question is None:
                    global_question = candidate.name
            question_by_code[code] = selected or global_question or candidates[0].name

        selected_names = sorted(set(question_by_code.values()))
        impacts = {
            (row.parent, row.option_value): row.price_impact_percent
            for row in frappe.get_all(
                "Buyback Question Option",
                filters={"parent": ["in", selected_names]},
                fields=["parent", "option_value", "price_impact_percent"],
            )
        }
        return {"question_by_code": question_by_code, "impacts": impacts}

    def _fill_response_impacts(self, cache=None):
        """Look up price_impact_percent from Question Bank options for each response."""
        cache = cache or self._load_question_impact_cache()
        for r in self.responses:
            if not r.question_code or not r.answer_value:
                continue

            qname = cache["question_by_code"].get(r.question_code)
            if not qname:
                continue

            impact = cache["impacts"].get((qname, r.answer_value))
            if impact is not None:
                r.price_impact_percent = impact

    def _fill_diagnostic_impacts(self, cache=None):
        """Look up depreciation_percent from Question Bank options for each diagnostic test.

        Automated tests store results as Pass/Fail/Partial which map to
        option_value in the Question Bank options table.
        """
        cache = cache or self._load_question_impact_cache()
        for d in self.diagnostic_tests:
            if not d.test_code or not d.result:
                continue

            qname = cache["question_by_code"].get(d.test_code)
            if not qname:
                continue

            impact = cache["impacts"].get((qname, d.result))
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
                is_phone_dead=bool(self.get("is_phone_dead")),
            )

            # When the engine can't price the item at all (no Buyback Price
            # Master row → base_price 0), keep any pre-set estimate instead
            # of clobbering it with 0 (e.g. demo/imported assessments).
            if result.get("base_price") or not self.estimated_price:
                self.estimated_price = result.get("estimated_price", 0)
            final_grade_letter = result.get("grade_letter") or "A"
            final_grade_id = frappe.db.get_value(
                "Grade Master", {"grade_name": final_grade_letter}, "name"
            )
            if final_grade_id:
                self.estimated_grade = final_grade_id
        except (ValueError, KeyError, frappe.ValidationError, frappe.DoesNotExistError):
            frappe.log_error(title=f"Assessment pricing failed: {self.name}")

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    def submit_assessment(self):
        """Customer finalises the self-assessment."""
        require_scoped_document_action(
            self, "assessment_operation_roles", _("submit a Buyback assessment")
        )
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

    @frappe.whitelist(methods=["POST"])
    def submit_imei_validation(self, status: str, screenshot: str | None = None, remarks: str | None = None) -> dict:
        """Record the manual Sanchar Saathi (CEIR) IMEI check result at intake.

        Optional at this stage (the hard gate is `create_inspection()` below
        and, redundantly, the Buyback Order gate) — doing it here means the
        store doesn't waste an inspector's time grading a device that turns
        out to be reported lost/stolen nationally. If a Buyback Order is
        later created from this assessment, the result is carried forward
        automatically so staff don't have to repeat the check.
        """
        require_scoped_document_action(
            self,
            "assessment_operation_roles",
            _("record an assessment IMEI validation result"),
        )
        allowed = {"Verified Clean", "Blacklisted", "Duplicate IMEI", "Already In Use", "Could Not Verify"}
        status = (status or "").strip()
        if status not in allowed:
            frappe.throw(
                _("Invalid validation status: {0}. Allowed: {1}").format(
                    status, ", ".join(sorted(allowed))
                ),
                exc=BuybackStatusError,
            )

        bad_outcomes = {"Blacklisted", "Duplicate IMEI", "Already In Use"}
        if status in bad_outcomes or status == "Verified Clean":
            if not screenshot:
                frappe.throw(
                    _("A screenshot of the Sanchar Saathi result is required for status {0}.").format(status),
                    exc=BuybackStatusError,
                )
        elif status == "Could Not Verify" and not (remarks or "").strip():
            frappe.throw(
                _("Remarks are required when the portal could not be checked (e.g. portal down, no response)."),
                exc=BuybackStatusError,
            )

        self.imei_validation_status = status
        self.imei_validation_checked_by = frappe.session.user
        self.imei_validation_checked_at = now_datetime()
        if screenshot:
            self.imei_validation_screenshot = screenshot
        if remarks is not None:
            self.imei_validation_remarks = remarks
        self.flags.ignore_mandatory = True
        self.save()

        audit_action = "IMEI Validation Completed" if status == "Verified Clean" else "IMEI Validation Failed"
        log_audit(audit_action, "Buyback Assessment", self.name,
                  new_value={"status": status, "checked_by": self.imei_validation_checked_by})

        result = {
            "name": self.name,
            "imei_validation_status": status,
            "blocked": status != "Verified Clean",
            "status": self.status,
        }

        if status in bad_outcomes:
            # Definitive national-registry hit — cancel outright, same severity
            # as the internal blacklist check, so no inspection effort follows.
            self.cancel_assessment()
            log_audit("Assessment Cancelled", "Buyback Assessment", self.name,
                      new_value={"reason": f"IMEI {status} on Sanchar Saathi"})
            result["status"] = self.status
            result["message"] = _(
                "Device flagged as '{0}' on the Sanchar Saathi national registry. "
                "This assessment has been cancelled."
            ).format(status)
        elif status == "Could Not Verify":
            result["message"] = _("Could not verify on Sanchar Saathi. Please retry the check before proceeding.")
        else:
            result["message"] = _("IMEI verified clean. You may proceed with inspection.")

        return result

    def create_inspection(self, checklist_template=None):
        """Create a Buyback Inspection directly from this assessment.

        Copies diagnostic tests and question responses into the new
        side-by-side inspection tables.  When source is "Store Manual"
        (POS in-store), the inspector columns are pre-filled with the
        same data so the store person doesn't have to re-enter answers.

        Returns the new Buyback Inspection doc.
        """
        require_scoped_document_action(
            self, "assessment_operation_roles", _("create a Buyback inspection")
        )
        frappe.has_permission("Buyback Inspection", ptype="create", throw=True)
        if self.status not in ("Draft", "Submitted"):
            frappe.throw(
                _("Can only create inspection from a Draft or Submitted assessment."),
                exc=BuybackStatusError,
            )

        if self.imei_validation_status != "Verified Clean" or not self.imei_validation_screenshot:
            frappe.throw(
                _("Sanchar Saathi IMEI validation must be completed (status = 'Verified Clean', "
                  "with screenshot) before inspection can start. Current status: {0}.").format(
                    self.imei_validation_status or "Pending"
                ),
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
        inspection.ch_customer_id = self.ch_customer_id
        inspection.ch_membership_id = self.ch_membership_id

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

        # For POS, also pre-fill the post-inspection grade and set inspector to current user
        if is_pos:
            inspection.post_inspection_grade = self.estimated_grade
            inspection.revised_price = self.quoted_price or self.estimated_price
            inspection.inspector = frappe.session.user

        inspection.insert()

        if checklist_template:
            inspection.populate_checklist()
            inspection.flags.ignore_mandatory = True
            inspection.save()
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

    @frappe.whitelist(methods=["POST"])
    def mark_customer_interested(self):
        """Called when customer taps 'Sell Now' in the mobile app or kiosk.

        Idempotent — safe to call multiple times; only sets the flag once.
        Returns the current assessment state so the caller can read
        quoted_price, expires_on, etc. in a single round trip.
        """
        require_scoped_document_action(
            self,
            "assessment_operation_roles",
            _("mark a customer interested in a Buyback quote"),
        )
        if not self.customer_interested:
            self.customer_interested = 1
            self.interested_at = now_datetime()
            # allow_on_submit is set on both fields so this works for submitted docs too
            self.flags.ignore_mandatory = True
            self.save(ignore_permissions=False)
            log_audit(
                "Customer Interested",
                "Buyback Assessment", self.name,
                new_value={"interested_at": str(self.interested_at)},
            )

        return {
            "assessment": self.name,
            "assessment_id": self.assessment_id,
            "customer": self.customer,
            "ch_customer_id": self.ch_customer_id,
            "ch_membership_id": self.ch_membership_id,
            "item": self.item,
            "item_name": self.item_name,
            "quoted_price": self.quoted_price or self.estimated_price,
            "estimated_grade": self.estimated_grade,
            "expires_on": str(self.expires_on) if self.expires_on else None,
            "customer_interested": self.customer_interested,
            "interested_at": str(self.interested_at) if self.interested_at else None,
            "status": self.status,
        }


@frappe.whitelist(methods=["POST"])
def mark_interested(assessment_name):
    """REST-friendly wrapper — callable without a document instance.

    Endpoint: POST /api/method/buyback.buyback.doctype.buyback_assessment.buyback_assessment.mark_interested
    Body: { "assessment_name": "BBA-2026-00001" }
    """
    doc = frappe.get_doc("Buyback Assessment", assessment_name)
    return doc.mark_customer_interested()
