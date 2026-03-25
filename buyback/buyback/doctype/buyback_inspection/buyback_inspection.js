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

        // ── Load dropdown options for inspector diagnostic & response rows ──
        _load_inspection_diagnostic_options(frm);
        _load_inspection_response_options(frm);

        // ── Per-row result options for legacy Results table ──
        _setup_result_row_options(frm);
    },
});

// ═══════════════════════════════════════════════════════════════════
// INSPECTION DIAGNOSTICS — load options & handle inspector_result
// ═══════════════════════════════════════════════════════════════════

function _load_inspection_diagnostic_options(frm) {
    (frm.doc.inspection_diagnostics || []).forEach(row => {
        if (!row.test) return;
        frappe.call({
            method: "buyback.api.get_question_options",
            args: { question_name: row.test },
            async: false,
            callback(r) {
                if (!r.message || !r.message.length) return;
                row._impact_map = {};
                const opts = [];
                r.message.forEach(o => {
                    opts.push(o.option_value);
                    row._impact_map[o.option_value] = Math.abs(o.price_impact_percent || 0);
                });
                row._result_options = opts;
            },
        });
    });

    // Set options on the grid for the current active row via click handler
    if (frm.fields_dict.inspection_diagnostics) {
        const grid = frm.fields_dict.inspection_diagnostics.grid;
        grid.wrapper.off("click.insp_diag_opts");
        grid.wrapper.on("click.insp_diag_opts", "[data-idx]", function () {
            const idx = $(this).attr("data-idx") || $(this).closest("[data-idx]").attr("data-idx");
            if (!idx) return;
            const row = frm.doc.inspection_diagnostics[parseInt(idx) - 1];
            if (row && row._result_options && row._result_options.length) {
                grid.update_docfield_property(
                    "inspector_result", "options", ["", ...row._result_options].join("\n")
                );
            }
        });
    }
}

frappe.ui.form.on("Buyback Inspection Diagnostic", {
    inspector_result(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (!row.inspector_result || !row.test) return;

        // Try local cache first
        if (row._impact_map && row._impact_map.hasOwnProperty(row.inspector_result)) {
            frappe.model.set_value(cdt, cdn, "inspector_depreciation",
                row._impact_map[row.inspector_result]
            );
            return;
        }

        // Fallback: fetch from server
        frappe.call({
            method: "buyback.api.get_question_options",
            args: { question_name: row.test },
            callback(r) {
                if (!r.message) return;
                const opt = r.message.find(o => o.option_value === row.inspector_result);
                if (opt) {
                    frappe.model.set_value(cdt, cdn, "inspector_depreciation",
                        Math.abs(opt.price_impact_percent || 0)
                    );
                }
            },
        });
    },
});

// ═══════════════════════════════════════════════════════════════════
// INSPECTION RESPONSES — load options & handle inspector_answer
// ═══════════════════════════════════════════════════════════════════

function _load_inspection_response_options(frm) {
    (frm.doc.inspection_responses || []).forEach(row => {
        if (!row.question) return;
        frappe.call({
            method: "buyback.api.get_question_options",
            args: { question_name: row.question },
            async: false,
            callback(r) {
                if (!r.message || !r.message.length) return;
                row._options_map = {};
                row._impact_map = {};
                const opts = [];
                r.message.forEach(o => {
                    opts.push(o.option_value);
                    row._options_map[o.option_value] = o.option_label || o.option_value;
                    row._impact_map[o.option_value] = o.price_impact_percent || 0;
                });
                row._answer_options = opts;
            },
        });
    });

    // Also populate assessment_answer options so saved values display correctly
    if (frm.fields_dict.inspection_responses) {
        const grid = frm.fields_dict.inspection_responses.grid;

        // Build union of all answer options for assessment_answer display
        const all_opts = new Set();
        (frm.doc.inspection_responses || []).forEach(row => {
            if (row.assessment_answer) all_opts.add(row.assessment_answer);
            (row._answer_options || []).forEach(o => all_opts.add(o));
        });
        if (all_opts.size) {
            grid.update_docfield_property(
                "assessment_answer", "options", ["", ...all_opts].join("\n")
            );
        }

        // Per-row click handler for inspector_answer dropdown
        grid.wrapper.off("click.insp_resp_opts");
        grid.wrapper.on("click.insp_resp_opts", "[data-idx]", function () {
            const idx = $(this).attr("data-idx") || $(this).closest("[data-idx]").attr("data-idx");
            if (!idx) return;
            const row = frm.doc.inspection_responses[parseInt(idx) - 1];
            if (row && row._answer_options && row._answer_options.length) {
                grid.update_docfield_property(
                    "inspector_answer", "options", ["", ...row._answer_options].join("\n")
                );
            }
        });
    }
}

frappe.ui.form.on("Buyback Inspection Response", {
    inspector_answer(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (!row.inspector_answer || !row.question) return;

        // Set label from cache
        if (row._options_map) {
            frappe.model.set_value(cdt, cdn, "inspector_answer_label",
                row._options_map[row.inspector_answer] || row.inspector_answer
            );
        }

        // Set impact from cache
        if (row._impact_map && row._impact_map.hasOwnProperty(row.inspector_answer)) {
            frappe.model.set_value(cdt, cdn, "inspector_impact",
                row._impact_map[row.inspector_answer]
            );
            return;
        }

        // Fallback: fetch from server
        frappe.call({
            method: "buyback.api.get_question_options",
            args: { question_name: row.question },
            callback(r) {
                if (!r.message) return;
                const opt = r.message.find(o => o.option_value === row.inspector_answer);
                if (opt) {
                    frappe.model.set_value(cdt, cdn, "inspector_answer_label",
                        opt.option_label || opt.option_value
                    );
                    frappe.model.set_value(cdt, cdn, "inspector_impact",
                        opt.price_impact_percent || 0
                    );
                }
            },
        });
    },
});

// Dynamic result dropdown based on check_type — per-row via click handler
frappe.ui.form.on("Buyback Inspection Result", {
    results_add(frm, cdt, cdn) {
        const row = frappe.get_doc(cdt, cdn);
        _set_result_options(frm, row);
    },
});

function _setup_result_row_options(frm) {
    if (!frm.fields_dict.results) return;
    const grid = frm.fields_dict.results.grid;
    grid.wrapper.off("click.result_opts");
    grid.wrapper.on("click.result_opts", "[data-idx]", function () {
        const idx = $(this).attr("data-idx") || $(this).closest("[data-idx]").attr("data-idx");
        if (!idx) return;
        const row = frm.doc.results[parseInt(idx) - 1];
        if (row) _set_result_options(frm, row);
    });
}

function _set_result_options(frm, row) {
    if (!row.check_type) return;
    const opts = CHECK_TYPE_OPTIONS[row.check_type];
    if (opts) {
        const options_str = [""].concat(opts).join("\n");
        frm.fields_dict.results.grid.update_docfield_property(
            "result", "options", options_str
        );
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
