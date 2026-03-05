// Copyright (c) 2026, Congruence Holdings and contributors
// For license information, please see license.txt

frappe.ui.form.on("Buyback Exchange Order", {
    refresh(frm) {
        if (frm.doc.docstatus !== 1) return;

        if (frm.doc.status === "New Device Delivered") {
            frm.add_custom_button(__("Awaiting Pickup"), () => {
                frm.call("deliver_new_device").then(() => frm.reload_doc());
            }, __("Actions"));
        }
        if (frm.doc.status === "Awaiting Pickup") {
            frm.add_custom_button(__("Receive Old Device"), () => {
                frm.call("receive_old_device").then(() => frm.reload_doc());
            }, __("Actions"));
        }
        if (frm.doc.status === "Old Device Received") {
            frm.add_custom_button(__("Inspect"), () => {
                frm.call("inspect_old_device").then(() => frm.reload_doc());
            }, __("Actions"));
        }
        if (frm.doc.status === "Inspected") {
            frm.add_custom_button(__("Settle"), () => {
                frm.call("settle").then(() => frm.reload_doc());
            }, __("Actions"));
        }
        if (frm.doc.status === "Settled") {
            frm.add_custom_button(__("Close"), () => {
                frm.call("close").then(() => frm.reload_doc());
            }, __("Actions"));
        }

        const colors = {
            "Draft": "red",
            "New Device Delivered": "blue",
            "Awaiting Pickup": "orange",
            "Old Device Received": "blue",
            "Inspected": "blue",
            "Settled": "green",
            "Closed": "green",
            "Cancelled": "grey"
        };
        frm.page.set_indicator(frm.doc.status, colors[frm.doc.status] || "grey");
    },

    new_device_price(frm) {
        _recalculate(frm);
    },
    buyback_amount(frm) {
        _recalculate(frm);
    },
    exchange_discount(frm) {
        _recalculate(frm);
    },
});

function _recalculate(frm) {
    let amount = Math.max(0,
        (frm.doc.new_device_price || 0)
        - (frm.doc.buyback_amount || 0)
        - (frm.doc.exchange_discount || 0)
    );
    frm.set_value("amount_to_pay", amount);
}
