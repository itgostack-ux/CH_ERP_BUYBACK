import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate


class BuybackPricingRule(Document):
    def before_insert(self):
        """Auto-assign sequential integer ID using advisory lock."""
        frappe.db.sql("SELECT GET_LOCK('buyback_pricing_rule_id', 10)")
        try:
            last = frappe.db.sql(
                "SELECT MAX(pricing_rule_id) FROM `tabBuyback Pricing Rule`"
            )[0][0] or 0
            self.pricing_rule_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('buyback_pricing_rule_id')")

    def validate(self):
        self._validate_deduction_values()
        self._validate_slabs()
        self._validate_validity()

    def _validate_deduction_values(self):
        if self.rule_type == "Flat Deduction" and (self.flat_deduction or 0) <= 0:
            frappe.throw(
                _("Flat Deduction amount must be greater than zero."),
                title=_("Invalid Deduction"),
            )
        if self.rule_type == "Percentage Deduction" and (
            (self.percent_deduction or 0) <= 0 or (self.percent_deduction or 0) > 100
        ):
            frappe.throw(
                _("Percentage Deduction must be between 0 and 100."),
                title=_("Invalid Deduction"),
            )

    def _validate_slabs(self):
        if self.rule_type == "Slab-Based" and self.slabs:
            prev_to = 0
            for slab in sorted(self.slabs, key=lambda s: s.from_amount):
                if slab.from_amount < 0 or slab.to_amount < 0:
                    frappe.throw(_("Slab amounts cannot be negative."))
                if slab.from_amount >= slab.to_amount:
                    frappe.throw(_("'From Amount' must be less than 'To Amount' in slabs."))
                if slab.from_amount < prev_to:
                    frappe.throw(_("Pricing slabs must not overlap."))
                prev_to = slab.to_amount

    def _validate_validity(self):
        if self.valid_from and self.valid_to:
            if getdate(self.valid_from) > getdate(self.valid_to):
                frappe.throw(_("'Valid From' cannot be after 'Valid To'."))

    def is_applicable(self, brand=None, category=None, grade=None,
                      warranty=None, age_months=None, amount=None):
        """Check if this rule applies to the given conditions."""
        if self.disabled:
            return False

        # Check validity period
        today = getdate(nowdate())
        if self.valid_from and today < getdate(self.valid_from):
            return False
        if self.valid_to and today > getdate(self.valid_to):
            return False

        # Check matching conditions
        if self.applies_to_brand and self.applies_to_brand != brand:
            return False
        if self.applies_to_category and self.applies_to_category != category:
            return False
        if self.applies_to_grade and self.applies_to_grade != grade:
            return False
        if self.warranty_status and self.warranty_status != warranty:
            return False

        # Check age range
        if age_months is not None:
            if self.min_age_months and age_months < self.min_age_months:
                return False
            if self.max_age_months and age_months > self.max_age_months:
                return False

        return True

    def calculate_deduction(self, base_amount):
        """Calculate deduction amount based on rule type."""
        if self.rule_type == "Flat Deduction":
            return self.flat_deduction or 0

        elif self.rule_type == "Percentage Deduction":
            return base_amount * (self.percent_deduction or 0) / 100

        elif self.rule_type == "Slab-Based" and self.slabs:
            for slab in sorted(self.slabs, key=lambda s: s.from_amount):
                if slab.from_amount <= base_amount <= slab.to_amount:
                    return base_amount * slab.deduction_percent / 100
            return 0

        return 0
