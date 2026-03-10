// Copyright (c) 2026, Congruence Holdings and contributors
// For license information, please see license.txt

const CHECK_TYPE_OPTIONS = {
    "Pass/Fail": ["Pass", "Fail"],
    "Grade (A/B/C/D)": ["A", "B", "C", "D"],
    "Yes/No": ["Yes", "No"],
    "Condition": ["Good", "Fair", "Poor"],
};

frappe.ui.form.on("Buyback Inspection", {
    refresh(frm) {
        if (frm.doc.status === "Draft" && !frm.is_new()) {
            frm.add_custom_button(__("Start Inspection"), () => {
                frm.call("start_inspection").then(() => frm.reload_doc());
            }, __("Actions"));
            frm.add_custom_button(__("Load Checklist"), () => {
                frm.call("populate_checklist").then(() => {
                    frm.dirty();
                    frm.refresh_fields();
                });
            }, __("Actions"));
        }
        if (frm.doc.status === "In Progress") {
            frm.add_custom_button(__("Complete"), () => {
                frm.call("complete_inspection").then(() => frm.reload_doc());
            }, __("Actions"));
            frm.add_custom_button(__("Reject Device"), () => {
                frappe.prompt({
                    label: "Rejection Reason",
                    fieldname: "reason",
                    fieldtype: "Small Text",
                    reqd: 1,
                }, (values) => {
                    frm.call("reject_device", { reason: values.reason }).then(() => frm.reload_doc());
                });
            }, __("Actions"));
        }
        if (frm.doc.status === "Completed") {
            frm.add_custom_button(__("Create Order"), () => {
                frappe.new_doc("Buyback Order", {
                    buyback_assessment: frm.doc.buyback_assessment,
                    buyback_inspection: frm.doc.name,
                    customer: frm.doc.customer,
                    store: frm.doc.store,
                    item: frm.doc.item,
                    imei_serial: frm.doc.imei_serial,
                    condition_grade: frm.doc.condition_grade,
                    final_price: frm.doc.revised_price || frm.doc.quoted_price,
                });
            }, __("Actions"));
        }

        const colors = {
            "Draft": "red",
            "In Progress": "orange",
            "Completed": "green",
            "Rejected": "grey"
        };
        frm.page.set_indicator(frm.doc.status, colors[frm.doc.status] || "grey");

        // Colour comparison rows
        _colour_comparison_rows(frm);

        // Set result dropdown options based on check_type for each row
        (frm.doc.results || []).forEach((row, idx) => {
            _set_result_options(frm, row);
        });
    },
});

// Dynamic result dropdown based on check_type
frappe.ui.form.on("Buyback Inspection Result", {
    check_type(frm, cdt, cdn) {
        const row = frappe.get_doc(cdt, cdn);
        _set_result_options(frm, row);
    },
    results_add(frm, cdt, cdn) {
        const row = frappe.get_doc(cdt, cdn);
        _set_result_options(frm, row);
    },
});

function _set_result_options(frm, row) {
    if (!row.check_type) return;
    const opts = CHECK_TYPE_OPTIONS[row.check_type];
    if (opts) {
        const options_str = [""].concat(opts).join("\n");
        frm.fields_dict.results.grid.update_docfield_property(
            "result", "options", options_str
        );
        frm.fields_dict.results.grid.refresh();
    }
}

function _colour_comparison_rows(frm) {
    // Highlight mismatch rows in the comparison table in red
    if (!frm.fields_dict.comparison_results) return;
    setTimeout(() => {
        frm.fields_dict.comparison_results.grid.grid_rows.forEach((grid_row) => {
            if (grid_row.doc.match_status === "Mismatch") {
                $(grid_row.row).css("background-color", "#fff0f0");
            } else if (grid_row.doc.match_status === "Match") {
                $(grid_row.row).css("background-color", "#f0fff0");
            }
        });
    }, 200);
}
