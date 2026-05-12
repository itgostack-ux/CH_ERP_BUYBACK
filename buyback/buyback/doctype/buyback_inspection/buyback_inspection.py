import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, flt

from buyback.utils import validate_indian_phone

from buyback.exceptions import BuybackStatusError
from buyback.utils import log_audit


class BuybackInspection(Document):
    def before_insert(self):
        """Auto-assign sequential integer ID using advisory lock."""
        frappe.db.sql("SELECT GET_LOCK('buyback_inspection_id', 10)")
        try:
            last = frappe.db.sql(
                "SELECT MAX(inspection_id) FROM `tabBuyback Inspection`"
            )[0][0] or 0
            self.inspection_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('buyback_inspection_id')")

        self.status = "Draft"

    def validate(self):
        if self.mobile_no:
            self.mobile_no = validate_indian_phone(self.mobile_no, "Mobile No")
        self._sync_customer_id()
        qbank_cache = self._load_question_bank_cache()
        self._validate_diagnostic_ranges(qbank_cache)
        self._fill_inspector_diagnostic_impacts(qbank_cache)
        self._fill_inspector_response_impacts(qbank_cache)
        self._set_condition_grade()
        # Recalculate price on every save when inspection is active
        if self.buyback_assessment and self.status in ("In Progress", "Draft") and self.condition_grade:
            self._recalculate_price()

    def _sync_customer_id(self):
        """Populate ch_customer_id and ch_membership_id from the linked Customer.

        fetch_from fires only in the browser; this ensures API/programmatic
        document creation also gets the values.
        """
        if not self.customer or (self.ch_customer_id and self.ch_membership_id):
            return
        cust = frappe.db.get_value(
            "Customer", self.customer,
            ["ch_customer_id", "ch_membership_id"],
            as_dict=True,
        )
        if cust:
            if not self.ch_customer_id:
                self.ch_customer_id = cust.ch_customer_id
            if not self.ch_membership_id:
                self.ch_membership_id = cust.ch_membership_id

    def _load_question_bank_cache(self):
        """Batch-load all Question Bank entries and their options needed by this inspection."""
        diag_codes = [d.test_code for d in (self.inspection_diagnostics or []) if d.test_code]
        resp_codes = [r.question_code for r in (self.inspection_responses or []) if r.question_code]
        all_codes = list(set(diag_codes + resp_codes))
        if not all_codes:
            return {}

        rows = frappe.db.sql(
            """
            SELECT name, question_code, question_text, min_value, max_value
            FROM `tabBuyback Question Bank`
            WHERE question_code IN %(codes)s AND disabled = 0
            """,
            {"codes": tuple(all_codes)},
            as_dict=True,
        )
        qbank = {r.question_code: r for r in rows}

        if not qbank:
            return {}

        qnames = [r.name for r in rows]
        options = frappe.db.sql(
            """
            SELECT parent, option_value, price_impact_percent
            FROM `tabBuyback Question Option`
            WHERE parent IN %(names)s
            """,
            {"names": tuple(qnames)},
            as_dict=True,
        )
        # Map: question_code → {option_value → price_impact_percent}
        opts_by_code = {}
        qname_to_code = {r.name: r.question_code for r in rows}
        for opt in options:
            code = qname_to_code.get(opt.parent)
            if code:
                opts_by_code.setdefault(code, {})[opt.option_value] = opt.price_impact_percent

        return {"qbank": qbank, "options": opts_by_code}

    def _validate_diagnostic_ranges(self, qbank_cache=None):
        """BB-2 fix: Validate numeric diagnostic results fall within acceptable ranges."""
        qbank = (qbank_cache or {}).get("qbank", {})
        for d in (self.inspection_diagnostics or []):
            result = d.inspector_result or d.assessment_result
            if not result or not d.test_code:
                continue
            qdata = qbank.get(d.test_code)
            if not qdata:
                continue
            if qdata.min_value is not None or qdata.max_value is not None:
                try:
                    num_result = flt(result)
                    if qdata.min_value is not None and num_result < flt(qdata.min_value):
                        frappe.throw(
                            _("Diagnostic '{0}': result {1} is below minimum ({2})").format(
                                qdata.question_text or d.test_code, result, qdata.min_value),
                            title=_("Diagnostic Range Error"),
                        )
                    if qdata.max_value is not None and num_result > flt(qdata.max_value):
                        frappe.throw(
                            _("Diagnostic '{0}': result {1} exceeds maximum ({2})").format(
                                qdata.question_text or d.test_code, result, qdata.max_value),
                            title=_("Diagnostic Range Error"),
                        )
                except (ValueError, TypeError):
                    pass  # Non-numeric result — skip range check

    def _set_condition_grade(self):
        """Set final condition grade from inspector diagnostics or post-inspection grade.

        Auto-determination always runs when inspector has filled in diagnostic results.
        The inspector's manual `post_inspection_grade` choice is protected only when a
        `grade_changed_reason` has been entered — signalling an explicit override.
        """
        auto_grade = None

        # Always try to auto-determine from inspector diagnostic results
        if self.inspection_diagnostics:
            diagnostic_data = []
            for d in self.inspection_diagnostics:
                result = d.inspector_result or d.assessment_result
                if result and d.test_code:
                    diagnostic_data.append({
                        "test_code": d.test_code,
                        "result": result,
                    })
            if diagnostic_data:
                try:
                    from buyback.api import _auto_determine_grade
                    auto_grade = _auto_determine_grade(diagnostic_data)
                except Exception:
                    frappe.log_error(
                        title=f"Auto-grade determination failed for {self.name}",
                        message=frappe.get_traceback(),
                    )

        if auto_grade:
            # Resolve grade letter ("A"/"B"/"C"/"D") to Grade Master record name.
            # _auto_determine_grade returns a plain letter, but condition_grade and
            # post_inspection_grade are Link → Grade Master fields which need the
            # docname, not the letter.
            auto_grade_name = (
                frappe.db.get_value("Grade Master", {"grade_name": auto_grade}, "name")
                or auto_grade
            )
            # Respect inspector's explicit override (signalled by grade_changed_reason)
            if self.grade_changed_reason and self.post_inspection_grade:
                self.condition_grade = self.post_inspection_grade
            else:
                # Update post_inspection_grade with auto-determined value
                self.post_inspection_grade = auto_grade_name
                self.condition_grade = auto_grade_name
        elif self.post_inspection_grade:
            self.condition_grade = self.post_inspection_grade
        elif self.pre_inspection_grade:
            self.condition_grade = self.pre_inspection_grade

    def _fill_inspector_diagnostic_impacts(self, qbank_cache=None):
        """Look up depreciation_percent for inspector's re-test results."""
        opts = (qbank_cache or {}).get("options", {})
        for d in (self.inspection_diagnostics or []):
            if not d.inspector_result or not d.test_code:
                continue
            impact = (opts.get(d.test_code) or {}).get(d.inspector_result)
            if impact is not None:
                d.inspector_depreciation = abs(impact)

    def _fill_inspector_response_impacts(self, qbank_cache=None):
        """Look up price_impact_percent for inspector's re-assessment answers."""
        opts = (qbank_cache or {}).get("options", {})
        for r in (self.inspection_responses or []):
            if not r.inspector_answer or not r.question_code:
                continue
            impact = (opts.get(r.question_code) or {}).get(r.inspector_answer)
            if impact is not None:
                r.inspector_impact = impact

    @frappe.whitelist()
    def start_inspection(self):
        """Begin the inspection process."""
        if self.status != "Draft":
            frappe.throw(_("Can only start inspection from Draft status."), exc=BuybackStatusError, title=_("Buyback Inspection Error"))
        self.status = "In Progress"
        self.inspection_started_at = now_datetime()
        self.inspector = frappe.session.user
        self.save()
        log_audit("Inspection Started", "Buyback Inspection", self.name)

    @frappe.whitelist()
    def complete_inspection(self):
        """Complete the inspection with results."""
        if self.status != "In Progress":
            frappe.throw(_("Can only complete an In Progress inspection."), exc=BuybackStatusError, title=_("Buyback Inspection Error"))
        if not self.condition_grade:
            frappe.throw(_("Final Condition Grade is required to complete inspection."), title=_("Buyback Inspection Error"))
        self.status = "Completed"
        self.inspection_completed_at = now_datetime()
        self._build_comparison()
        self._recalculate_price()
        self.save()
        log_audit("Inspection Completed", "Buyback Inspection", self.name,
                  new_value={"grade": self.condition_grade, "revised_price": self.revised_price})

    # ── Comparison Logic ───────────────────────────────────────────
    def _build_comparison(self):
        """Build comparison from the side-by-side inspection_diagnostics
        and inspection_responses tables.  Falls back to the old
        assessment-vs-checklist comparison if the new tables are empty.
        """
        self.comparison_results = []
        total = 0
        mismatches = 0
        total_price_diff = 0

        # Compare diagnostic tests
        for d in (self.inspection_diagnostics or []):
            if not d.assessment_result and not d.inspector_result:
                continue
            total += 1
            assess = (d.assessment_result or "").strip().lower()
            insp = (d.inspector_result or "").strip().lower()
            match = assess == insp

            if not match:
                mismatches += 1

            price_diff = flt(d.inspector_depreciation or 0) - flt(d.assessment_depreciation or 0)
            total_price_diff += price_diff

            self.append("comparison_results", {
                "question": d.test,
                "question_code": d.test_code,
                "customer_answer": d.assessment_result or "",
                "inspector_answer": d.inspector_result or "",
                "match_status": "Match" if match else "Mismatch",
                "price_impact_difference": price_diff,
            })

        # Compare question responses
        for r in (self.inspection_responses or []):
            if not r.assessment_answer and not r.inspector_answer:
                continue
            total += 1
            assess = (r.assessment_answer or "").strip().lower()
            insp = (r.inspector_answer or "").strip().lower()
            match = assess == insp

            if not match:
                mismatches += 1

            price_diff = flt(r.inspector_impact or 0) - flt(r.assessment_impact or 0)
            total_price_diff += price_diff

            self.append("comparison_results", {
                "question": r.question,
                "question_code": r.question_code,
                "customer_answer": r.assessment_answer or "",
                "inspector_answer": r.inspector_answer or "",
                "match_status": "Match" if match else "Mismatch",
                "price_impact_difference": price_diff,
            })

        # If no data in new tables, fall back to old method
        if total == 0:
            self._build_comparison_legacy()
            return

        self.total_questions_compared = total
        self.total_mismatches = mismatches
        self.mismatch_percentage = (mismatches / total * 100) if total else 0
        self.price_variance_from_comparison = total_price_diff

    def _build_comparison_legacy(self):
        """Legacy comparison: assessment responses vs inspector checklist results."""
        if not self.buyback_assessment:
            return

        assessment = frappe.get_doc("Buyback Assessment", self.buyback_assessment)
        if not assessment.responses:
            return

        customer_map = {}
        for r in assessment.responses:
            customer_map[r.question_code or r.question] = r

        inspector_map = {}
        for r in self.results:
            inspector_map[r.check_code or r.checklist_item] = r

        total = 0
        mismatches = 0
        total_price_diff = 0

        for key, cust_row in customer_map.items():
            insp_row = inspector_map.get(key)
            if not insp_row:
                continue

            total += 1
            cust_answer = (cust_row.answer_value or "").strip()
            insp_answer = (insp_row.result or "").strip()
            match = cust_answer.lower() == insp_answer.lower()

            if not match:
                mismatches += 1

            price_diff = flt(insp_row.get("price_impact") or 0) - flt(cust_row.price_impact_percent or 0)
            total_price_diff += price_diff

            self.append("comparison_results", {
                "question": cust_row.question,
                "question_code": key,
                "customer_answer": cust_answer,
                "inspector_answer": insp_answer,
                "match_status": "Match" if match else "Mismatch",
                "price_impact_difference": price_diff,
            })

        self.total_questions_compared = total
        self.total_mismatches = mismatches
        self.mismatch_percentage = (mismatches / total * 100) if total else 0
        self.price_variance_from_comparison = total_price_diff

    # ── Price Recalculation ────────────────────────────────────────
    def _recalculate_price(self):
        """Recalculate revised price using inspector's re-test data.

        Uses the inspector's answers from inspection_diagnostics and
        inspection_responses tables with the inspector's grade to get
        the revised price from the pricing engine.
        """
        if not self.buyback_assessment:
            return

        try:
            from buyback.buyback.pricing.engine import calculate_estimated_price

            assessment = frappe.get_doc("Buyback Assessment", self.buyback_assessment)

            # Build inspector diagnostic data from new table
            diagnostic_data = []
            for d in (self.inspection_diagnostics or []):
                result = d.inspector_result or d.assessment_result
                if result:
                    diagnostic_data.append({
                        "test": d.test,
                        "test_code": d.test_code,
                        "result": result,
                        "depreciation_percent": d.inspector_depreciation if d.inspector_result else d.assessment_depreciation,
                    })

            # Fall back to assessment diagnostics if new table is empty
            if not diagnostic_data:
                diagnostic_data = [
                    {"test_code": d.test_code, "result": d.result}
                    for d in (assessment.diagnostic_tests or [])
                ]

            # Build inspector response data from new table
            inspector_responses = []
            for r in (self.inspection_responses or []):
                answer = r.inspector_answer or r.assessment_answer
                if answer and r.question_code:
                    inspector_responses.append({
                        "question_code": r.question_code,
                        "answer_value": answer,
                    })

            # Fall back to checklist results → question mapping if new table empty
            if not inspector_responses:
                for row in (self.results or []):
                    code = row.check_code
                    if not code:
                        continue
                    q_name = frappe.db.get_value(
                        "Buyback Question Bank", {"question_code": code}, "name"
                    )
                    if q_name:
                        answer = (row.result or "").strip()
                        if answer.lower() in ("pass", "yes"):
                            answer = "yes"
                        elif answer.lower() in ("fail", "no"):
                            answer = "no"
                        inspector_responses.append({
                            "question_code": code,
                            "answer_value": answer,
                        })

            # Final fallback: use assessment responses as-is
            if not inspector_responses:
                inspector_responses = [
                    {"question_code": r.question_code, "answer_value": r.answer_value}
                    for r in (assessment.responses or [])
                ]

            pricing = calculate_estimated_price(
                item_code=assessment.item,
                grade=self.condition_grade,
                warranty_status=self.warranty_status or assessment.warranty_status,
                device_age_months=self.device_age_months or assessment.device_age_months,
                responses=inspector_responses,
                diagnostic_tests=diagnostic_data,
                brand=self.brand or assessment.brand,
                item_group=self.item_group or assessment.item_group,
            )

            assessed_price = assessment.quoted_price or assessment.estimated_price
            new_price = pricing.get("estimated_price", 0)
            if new_price and new_price != flt(assessed_price):
                self.revised_price = new_price
                self.price_variance_from_comparison = round(
                    (new_price - flt(assessed_price)) / max(flt(assessed_price), 1) * 100, 2
                )
            elif new_price:
                self.revised_price = new_price

        except (ValueError, KeyError, frappe.ValidationError, frappe.DoesNotExistError):
            frappe.log_error(
                title=f"Inspection price recalc failed for {self.name}",
            )

    @frappe.whitelist()
    def reject_device(self, reason=None):
        """Reject the device during inspection."""
        if self.status not in ("Draft", "In Progress"):
            frappe.throw(
                _("Cannot reject — inspection is already {0}.").format(self.status),
                exc=BuybackStatusError,
            )
        self.status = "Rejected"
        self.inspection_completed_at = now_datetime()
        if reason:
            self.remarks = (self.remarks or "") + f"\nRejection: {reason}"
        self.save()
        log_audit("Inspection Rejected", "Buyback Inspection", self.name,
                  new_value={"status": "Rejected", "reason": reason})

    @frappe.whitelist()
    def populate_checklist(self):
        """Auto-populate inspection results from the selected checklist template."""
        if not self.checklist_template:
            return
        template = frappe.get_doc("Buyback Checklist Template", self.checklist_template)
        self.results = []
        for item in template.items:
            self.append("results", {
                "checklist_item": item.check_item,
                "check_code": item.check_code,
                "check_type": item.check_type,
                "result": "",
            })

    @frappe.whitelist()
    def recalculate_grade_and_price(self):
        """Force re-run grade determination and price recalculation.
        Clears grade_changed_reason so auto-grade from diagnostics takes effect.
        """
        self.grade_changed_reason = None
        qbank_cache = self._load_question_bank_cache()
        self._fill_inspector_diagnostic_impacts(qbank_cache)
        self._fill_inspector_response_impacts(qbank_cache)
        self._set_condition_grade()
        if self.buyback_assessment and self.condition_grade:
            self._recalculate_price()
        self.save()
        return {
            "post_inspection_grade": self.post_inspection_grade,
            "condition_grade": self.condition_grade,
            "revised_price": self.revised_price,
        }

