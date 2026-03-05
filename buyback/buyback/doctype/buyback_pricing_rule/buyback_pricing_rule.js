// Copyright (c) 2026, Congruence Holdings and contributors
// For license information, please see license.txt

frappe.ui.form.on("Buyback Pricing Rule", {
    rule_type(frm) {
        // Clear irrelevant fields when rule type changes
        if (frm.doc.rule_type === "Flat Deduction") {
            frm.set_value("percent_deduction", 0);
        } else if (frm.doc.rule_type === "Percentage Deduction") {
            frm.set_value("flat_deduction", 0);
        }
    },
});
