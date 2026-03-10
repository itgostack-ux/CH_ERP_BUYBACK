import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, flt

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
        self._set_condition_grade()

    def _set_condition_grade(self):
        """Set final condition grade to post-inspection grade if available."""
        if self.post_inspection_grade:
            self.condition_grade = self.post_inspection_grade
        elif self.pre_inspection_grade:
            self.condition_grade = self.pre_inspection_grade

    def start_inspection(self):
        """Begin the inspection process."""
        if self.status != "Draft":
            frappe.throw(_("Can only start inspection from Draft status."), exc=BuybackStatusError)
        self.status = "In Progress"
        self.inspection_started_at = now_datetime()
        self.inspector = frappe.session.user
        self.save()
        log_audit("Inspection Started", "Buyback Inspection", self.name)

    def complete_inspection(self):
        """Complete the inspection with results."""
        if self.status != "In Progress":
            frappe.throw(_("Can only complete an In Progress inspection."), exc=BuybackStatusError)
        if not self.condition_grade:
            frappe.throw(_("Final Condition Grade is required to complete inspection."))
        self.status = "Completed"
        self.inspection_completed_at = now_datetime()
        self._build_comparison()
        self._recalculate_price()
        self.save()
        log_audit("Inspection Completed", "Buyback Inspection", self.name,
                  new_value={"grade": self.condition_grade, "revised_price": self.revised_price})

    # ── Comparison Logic ───────────────────────────────────────────
    def _build_comparison(self):
        """Compare inspector results against customer self-assessment responses.

        If a linked Buyback Assessment exists (via quote), builds a row-by-row
        comparison of customer answers vs inspector answers and computes summary
        metrics.
        """
        if not self.buyback_assessment:
            return

        assessment = frappe.get_doc("Buyback Assessment", self.buyback_assessment)
        if not assessment.responses:
            return

        # Build lookup: question_code → customer answer row
        customer_map = {}
        for r in assessment.responses:
            customer_map[r.question_code or r.question] = r

        # Build lookup: check_code → inspector result row
        inspector_map = {}
        for r in self.results:
            inspector_map[r.check_code or r.checklist_item] = r

        self.comparison_results = []
        total = 0
        mismatches = 0
        total_price_diff = 0

        # Walk through customer answers and find matching inspector answers
        for key, cust_row in customer_map.items():
            insp_row = inspector_map.get(key)
            if not insp_row:
                continue  # no matching inspector question

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
        """Recalculate price using inspector's grade and the pricing engine.

        If the inspection has a linked assessment, uses the assessment's item and
        warranty info with the inspector's condition_grade to get the
        revised price from the pricing engine.  The inspector's checklist
        results are mapped back to question-style responses so the engine
        can apply the correct deductions.
        """
        if self.revised_price:
            # Inspector already set a manual revised price — keep it
            return

        if not self.buyback_assessment:
            return

        try:
            from buyback.buyback.pricing.engine import calculate_estimated_price

            assessment = frappe.get_doc("Buyback Assessment", self.buyback_assessment)

            # Map inspector results → question-style responses for pricing
            inspector_responses = []
            for row in (self.results or []):
                code = row.check_code
                if not code:
                    continue
                # Check if there's a matching question in Question Bank
                q_name = frappe.db.get_value(
                    "Buyback Question Bank", {"question_code": code}, "name"
                )
                if q_name:
                    # Map Pass → yes, Fail → no for pricing
                    answer = (row.result or "").strip()
                    if answer.lower() in ("pass", "yes"):
                        answer = "yes"
                    elif answer.lower() in ("fail", "no"):
                        answer = "no"
                    inspector_responses.append({
                        "question_code": code,
                        "answer_value": answer,
                    })

            # Build diagnostic test data from assessment
            diagnostic_data = [
                {"test_code": d.test_code, "result": d.result}
                for d in (assessment.diagnostic_tests or [])
            ]

            pricing = calculate_estimated_price(
                item_code=assessment.item,
                grade=self.condition_grade,
                warranty_status=assessment.warranty_status,
                device_age_months=assessment.device_age_months,
                responses=inspector_responses or [
                    {"question_code": r.question_code, "answer_value": r.answer_value}
                    for r in (assessment.responses or [])
                ],
                diagnostic_tests=diagnostic_data,
                brand=assessment.brand,
                item_group=assessment.item_group,
            )

            assessed_price = assessment.quoted_price or assessment.estimated_price
            new_price = pricing.get("estimated_price", 0)
            if new_price and new_price != flt(assessed_price):
                self.revised_price = new_price
                self.price_variance_pct = round(
                    (new_price - flt(assessed_price)) / max(flt(assessed_price), 1) * 100, 2
                )
            elif new_price:
                self.revised_price = new_price

        except Exception:
            frappe.log_error(
                title=f"Inspection price recalc failed for {self.name}",
                message=frappe.get_traceback(),
            )

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



