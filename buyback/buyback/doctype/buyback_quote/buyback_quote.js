// Copyright (c) 2026, Congruence Holdings and contributors
// For license information, please see license.txt

frappe.ui.form.on("Buyback Quote", {
    refresh(frm) {
        if (frm.doc.status === "Draft" && !frm.is_new()) {
            frm.add_custom_button(__("Generate Quote"), () => {
                frm.call("mark_quoted").then(() => frm.reload_doc());
            }, __("Actions"));
        }
        if (frm.doc.status === "Quoted") {
            frm.add_custom_button(__("Accept Quote"), () => {
                frm.call("mark_accepted").then(() => frm.reload_doc());
            }, __("Actions"));
            frm.add_custom_button(__("Expire"), () => {
                frm.call("mark_expired").then(() => frm.reload_doc());
            }, __("Actions"));
        }
        if (frm.doc.status === "Accepted") {
            frm.add_custom_button(__("Create Inspection"), () => {
                frappe.new_doc("Buyback Inspection", {
                    buyback_quote: frm.doc.name,
                });
            }, __("Actions"));
        }

        // Status indicator
        const colors = {
            "Draft": "red",
            "Quoted": "blue",
            "Accepted": "green",
            "Expired": "grey",
            "Cancelled": "grey"
        };
        frm.page.set_indicator(frm.doc.status, colors[frm.doc.status] || "grey");
    },
});
