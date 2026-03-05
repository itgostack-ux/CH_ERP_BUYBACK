import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

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
        self.save()
        log_audit("Inspection Completed", "Buyback Inspection", self.name,
                  new_value={"grade": self.condition_grade, "revised_price": self.revised_price})

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



