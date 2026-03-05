// Copyright (c) 2026, Congruence Holdings and contributors
// For license information, please see license.txt

frappe.ui.form.on("Buyback Question Bank", {
    question_type(frm) {
        // Auto-populate Yes/No options
        if (frm.doc.question_type === "Yes/No" && (!frm.doc.options || frm.doc.options.length === 0)) {
            let yes_row = frm.add_child("options");
            yes_row.option_label = "Yes";
            yes_row.option_value = "yes";
            yes_row.price_impact_percent = 0;
            yes_row.is_default = 0;

            let no_row = frm.add_child("options");
            no_row.option_label = "No";
            no_row.option_value = "no";
            no_row.price_impact_percent = 0;
            no_row.is_default = 0;

            frm.refresh_field("options");
        }
    },
});
