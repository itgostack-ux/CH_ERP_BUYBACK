// Copyright (c) 2026, Congruence Holdings and contributors
// For license information, please see license.txt

frappe.ui.form.on("Buyback Order", {
    refresh(frm) {
        if (frm.doc.docstatus !== 1) return;

        if (frm.doc.status === "Awaiting Approval") {
            frm.add_custom_button(__("Approve"), () => {
                frappe.prompt({
                    label: "Remarks",
                    fieldname: "remarks",
                    fieldtype: "Small Text",
                }, (values) => {
                    frm.call("approve", { remarks: values.remarks }).then(() => frm.reload_doc());
                });
            }, __("Actions"));
            frm.add_custom_button(__("Reject"), () => {
                frappe.prompt({
                    label: "Rejection Reason",
                    fieldname: "remarks",
                    fieldtype: "Small Text",
                    reqd: 1,
                }, (values) => {
                    frm.call("reject", { remarks: values.remarks }).then(() => frm.reload_doc());
                });
            }, __("Actions"));
        }

        if (frm.doc.status === "Approved") {
            frm.add_custom_button(__("Send OTP"), () => {
                frm.call("send_otp").then((r) => {
                    if (r.message) {
                        frappe.msgprint(__("OTP sent to {0}", [frm.doc.mobile_no]));
                    }
                });
            }, __("Actions"));
        }

        if (frm.doc.status === "Awaiting OTP") {
            frm.add_custom_button(__("Verify OTP"), () => {
                frappe.prompt({
                    label: "Enter OTP",
                    fieldname: "otp_code",
                    fieldtype: "Data",
                    reqd: 1,
                }, (values) => {
                    frm.call("verify_otp", { otp_code: values.otp_code }).then((r) => {
                        if (r.message && r.message.valid) {
                            frappe.show_alert({ message: __("OTP Verified!"), indicator: "green" });
                            frm.reload_doc();
                        } else {
                            frappe.msgprint(r.message?.message || __("Invalid OTP"));
                        }
                    });
                });
            }, __("Actions"));
        }

        if (frm.doc.status === "OTP Verified") {
            frm.add_custom_button(__("Ready to Pay"), () => {
                frm.call("mark_ready_to_pay").then(() => frm.reload_doc());
            }, __("Actions"));
        }

        if (frm.doc.status === "Ready to Pay" && frm.doc.payment_status === "Paid") {
            frm.add_custom_button(__("Mark Paid"), () => {
                frm.call("mark_paid").then(() => frm.reload_doc());
            }, __("Actions"));
        }

        if (frm.doc.status === "Paid") {
            frm.add_custom_button(__("Close Order"), () => {
                frm.call("close").then(() => frm.reload_doc());
            }, __("Actions"));
            frm.add_custom_button(__("Create Exchange"), () => {
                frappe.new_doc("Buyback Exchange Order", {
                    buyback_order: frm.doc.name,
                    customer: frm.doc.customer,
                    mobile_no: frm.doc.mobile_no,
                    store: frm.doc.store,
                    old_item: frm.doc.item,
                    old_imei_serial: frm.doc.imei_serial,
                    old_condition_grade: frm.doc.condition_grade,
                    buyback_amount: frm.doc.final_price,
                });
            }, __("Actions"));
        }

        const colors = {
            "Draft": "red",
            "Awaiting Approval": "orange",
            "Approved": "blue",
            "Awaiting OTP": "orange",
            "OTP Verified": "blue",
            "Ready to Pay": "blue",
            "Paid": "green",
            "Closed": "green",
            "Rejected": "grey",
            "Cancelled": "grey"
        };
        frm.page.set_indicator(frm.doc.status, colors[frm.doc.status] || "grey");
    },
});
