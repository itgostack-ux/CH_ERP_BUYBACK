// ── CH Data Wipe Certificate ──────────────────────────────────────────────
// Renders convenience buttons on the form.
frappe.ui.form.on("CH Data Wipe Certificate", {
    refresh(frm) {
        if (!frm.doc.wiped_at) {
            frm.set_value("wiped_at", frappe.datetime.now_datetime());
        }
        if (!frm.doc.wiped_by) {
            frm.set_value("wiped_by", frappe.session.user);
        }

        if (frm.doc.docstatus === 1 && frm.doc.buyback_order) {
            frm.add_custom_button(__("Open Buyback Order"), () => {
                frappe.set_route("Form", "Buyback Order", frm.doc.buyback_order);
            });
        }
    },

    wipe_verified(frm) {
        if (frm.doc.wipe_verified) {
            if (!frm.doc.verified_at) {
                frm.set_value("verified_at", frappe.datetime.now_datetime());
            }
            if (!frm.doc.verified_by || frm.doc.verified_by === frm.doc.wiped_by) {
                frm.set_value("verified_by", null);
                frappe.msgprint({
                    title: __("Maker-Checker"),
                    message: __(
                        "The verifier must be a different user than the person who wiped the device."
                    ),
                    indicator: "orange",
                });
            }
        }
    },
});
