// ── CH Buyback Pickup Appointment ─────────────────────────────────────────
frappe.ui.form.on("CH Buyback Pickup Appointment", {
    refresh(frm) {
        if (frm.doc.docstatus === 1) {
            if (frm.doc.status === "Scheduled" || frm.doc.status === "Confirmed") {
                frm.add_custom_button(__("Mark En Route"), () => {
                    frappe.db.set_value(
                        frm.doctype,
                        frm.docname,
                        "status",
                        "En Route"
                    ).then(() => frm.reload_doc());
                });
                frm.add_custom_button(__("Mark Completed"), () => {
                    frappe.prompt(
                        [
                            {
                                fieldname: "remarks",
                                label: __("Remarks (optional)"),
                                fieldtype: "Small Text",
                            },
                        ],
                        (values) => {
                            frappe
                                .call({
                                    method: "buyback.lifecycle_api.complete_pickup",
                                    args: {
                                        appointment: frm.docname,
                                        remarks: values.remarks || "",
                                    },
                                })
                                .then(() => frm.reload_doc());
                        },
                        __("Complete Pickup"),
                        __("Confirm")
                    );
                });
                frm.add_custom_button(__("Mark Failed"), () => {
                    frappe.prompt(
                        [
                            {
                                fieldname: "failure_reason",
                                label: __("Failure Reason"),
                                fieldtype: "Select",
                                options: [
                                    "Customer Unavailable",
                                    "Wrong Address",
                                    "Device Not Ready",
                                    "Customer Refused Pickup",
                                    "Device Condition Mismatch",
                                    "Account Locked / Data Not Wiped",
                                    "Other",
                                ],
                                reqd: 1,
                            },
                            {
                                fieldname: "next_action",
                                label: __("Next Action"),
                                fieldtype: "Select",
                                options: [
                                    "Retry Same Slot",
                                    "Retry Different Slot",
                                    "Escalate to Manager",
                                    "Cancel Order",
                                ],
                                reqd: 1,
                            },
                            {
                                fieldname: "remarks",
                                label: __("Remarks"),
                                fieldtype: "Small Text",
                            },
                        ],
                        (values) => {
                            frappe
                                .call({
                                    method: "buyback.lifecycle_api.fail_pickup",
                                    args: {
                                        appointment: frm.docname,
                                        failure_reason: values.failure_reason,
                                        next_action: values.next_action,
                                        remarks: values.remarks || "",
                                    },
                                })
                                .then(() => frm.reload_doc());
                        },
                        __("Mark Attempt Failed"),
                        __("Save")
                    );
                });
            }

            if (
                frm.doc.status === "Attempted (Failed)" &&
                !frm.doc.reschedule_to &&
                frm.doc.next_action &&
                frm.doc.next_action.indexOf("Retry") === 0
            ) {
                frm.add_custom_button(__("Reschedule Pickup"), () => {
                    frappe.prompt(
                        [
                            {
                                fieldname: "appointment_date",
                                label: __("New Date"),
                                fieldtype: "Date",
                                reqd: 1,
                                default: frappe.datetime.add_days(
                                    frappe.datetime.get_today(),
                                    1
                                ),
                            },
                            {
                                fieldname: "appointment_slot",
                                label: __("Slot"),
                                fieldtype: "Select",
                                options: [
                                    "09:00 - 12:00 (Morning)",
                                    "12:00 - 15:00 (Afternoon)",
                                    "15:00 - 18:00 (Evening)",
                                    "18:00 - 21:00 (Late Evening)",
                                ],
                            },
                        ],
                        (values) => {
                            frappe
                                .call({
                                    method: "buyback.lifecycle_api.reschedule_pickup",
                                    args: {
                                        appointment: frm.docname,
                                        appointment_date: values.appointment_date,
                                        appointment_slot: values.appointment_slot || "",
                                    },
                                })
                                .then((r) => {
                                    if (r.message && r.message.name) {
                                        frappe.set_route(
                                            "Form",
                                            frm.doctype,
                                            r.message.name
                                        );
                                    }
                                });
                        },
                        __("Reschedule"),
                        __("Create Next Attempt")
                    );
                });
            }

            if (frm.doc.buyback_order) {
                frm.add_custom_button(__("Open Buyback Order"), () => {
                    frappe.set_route(
                        "Form",
                        "Buyback Order",
                        frm.doc.buyback_order
                    );
                });
            }
        }
    },
});
