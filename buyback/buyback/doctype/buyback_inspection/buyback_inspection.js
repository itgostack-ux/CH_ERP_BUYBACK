// Copyright (c) 2026, Congruence Holdings and contributors
// For license information, please see license.txt

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
                    buyback_quote: frm.doc.buyback_quote,
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
    },
});
