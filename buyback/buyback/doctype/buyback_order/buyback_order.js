// Copyright (c) 2026, Congruence Holdings and contributors
// For license information, please see license.txt

frappe.ui.form.on("Buyback Order", {
	    refresh(frm) {
	        frm.clear_custom_buttons();
	        if (frm.doc.docstatus !== 1) return;
	        const can_manager_approve = frappe.session.user === "Administrator"
	            || frappe.user.has_role("Buyback Manager")
	            || frappe.user.has_role("Buyback Admin")
	            || frappe.user.has_role("System Manager");

	        if (frm.doc.status === "Awaiting Approval" && can_manager_approve) {
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

            // Issue #3: customer has no phone — allow In-Store Signature as alternate approval
            frm.add_custom_button(__("Customer Approve (In-Store)"), () => {
                frappe.confirm(
                    __("Confirm customer has physically signed / approved the offer in-store?"),
                    () => {
                        frm.call("customer_approve", { method: "In-Store Signature" }).then(() => {
                            frappe.show_alert({ message: __("Customer approval recorded."), indicator: "green" });
                            frm.reload_doc();
                        });
                    }
                );
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

            frm.add_custom_button(__("Resend OTP"), () => {
                frm.call("send_otp").then((r) => {
                    if (r.message) {
                        frappe.show_alert({ message: __("OTP resent to {0}", [frm.doc.mobile_no]), indicator: "green" });
                    }
                });
            }, __("Actions"));

            frm.add_custom_button(__("Approve In-Store (Skip OTP)"), () => {
                frappe.prompt([{
                    label: __("Remarks"),
                    fieldname: "remarks",
                    fieldtype: "Small Text",
                    description: __("Reason for bypassing OTP — will be logged for audit"),
                }], (values) => {
                    frappe.confirm(
                        __("Confirm: customer is physically present and has approved in-store. OTP will be bypassed and this action will be logged."),
                        () => {
                            frm.call("bypass_otp_instore", { remarks: values.remarks }).then(() => {
                                frappe.show_alert({ message: __("In-store approval recorded — status set to OTP Verified"), indicator: "green" });
                                frm.reload_doc();
                            });
                        }
                    );
                }, __("In-Store Approval"));
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

            // Logistics Phase 1: pickup MR is now manual. Show the button
            // only when SE exists and no open pickup MR is already linked.
            if (frm.doc.stock_entry) {
                frappe.db.get_value(
                    "Material Request",
                    { custom_buyback_order: frm.doc.name, docstatus: ["<", 2] },
                    "name"
                ).then((r) => {
                    const existing = r && r.message && r.message.name;
                    if (existing) {
                        frm.add_custom_button(__("View Pickup MR ({0})", [existing]), () => {
                            frappe.set_route("Form", "Material Request", existing);
                        }, __("Logistics"));
                    } else {
                        frm.add_custom_button(__("Create Pickup Transfer Request"), () => {
                            frappe.confirm(
                                __("Raise a Material Transfer Request from this store's Buyback Bin to the central Buyback Bin so logistics can pick up the device?"),
                                () => {
                                    frm.call("create_pickup_request_now").then((res) => {
                                        if (res && res.message) {
                                            frm.reload_doc();
                                        }
                                    });
                                }
                            );
                        }, __("Logistics"));
                    }
                });
            }
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

// Store staff can request a buyback PRICE OVERRIDE on any saved order → routes
// to the Buyback Manager via the shared CH Exception framework. Kept in its own
// form handler so it shows regardless of order status/docstatus.
frappe.ui.form.on("Buyback Order", {
    refresh(frm) {
        if (frm.is_new()) return;
        frm.add_custom_button(__("Raise Price Exception"), () => {
            const current = frm.doc.approved_price || frm.doc.final_price || frm.doc.base_price || 0;
            const d = new frappe.ui.Dialog({
                title: __("Request Buyback Price Override"),
                fields: [
                    {
                        fieldname: "current_price",
                        fieldtype: "Currency",
                        label: __("Current Buyback Price"),
                        read_only: 1,
                        default: current,
                    },
                    {
                        fieldname: "requested_price",
                        fieldtype: "Currency",
                        label: __("Requested Buyback Price"),
                        reqd: 1,
                        default: current,
                    },
                    {
                        fieldname: "reason",
                        fieldtype: "Small Text",
                        label: __("Reason for the price change"),
                        reqd: 1,
                        description: __("Routed to the Buyback Manager for approval."),
                    },
                ],
                primary_action_label: __("Submit to Buyback Manager"),
                primary_action: (v) => {
                    d.hide();
                    frappe.call({
                        method: "buyback.api.raise_buyback_exception",
                        args: {
                            order: frm.doc.name,
                            requested_price: v.requested_price,
                            reason: v.reason,
                        },
                        freeze: true,
                        freeze_message: __("Submitting price override…"),
                        callback: (r) => {
                            if (r.message && r.message.name) {
                                frappe.show_alert({
                                    message: __("Price override {0} raised — routed to Buyback Manager.",
                                        [r.message.name]),
                                    indicator: "orange",
                                }, 7);
                            }
                        },
                    });
                },
            });
            d.show();
        });
    },
});
