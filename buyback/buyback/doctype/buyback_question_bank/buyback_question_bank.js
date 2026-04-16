// Copyright (c) 2026, Congruence Holdings and contributors
// For license information, please see license.txt

frappe.ui.form.on("Buyback Question Bank", {
    question_text(frm) {
        // Auto-generate question_code from question_text if blank or unchanged
        if (frm.doc.question_text && !frm.doc.question_code) {
            frm.set_value("question_code", frm.doc.question_text
                .trim().toLowerCase()
                .replace(/[^a-z0-9\s_]/g, "")
                .replace(/\s+/g, "_")
                .substring(0, 140)
            );
        }
    },
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
