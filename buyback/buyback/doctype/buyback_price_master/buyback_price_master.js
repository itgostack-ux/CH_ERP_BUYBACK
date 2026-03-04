// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.ui.form.on("Buyback Price Master", {
    refresh(frm) {
        frm.set_intro(
            __("Buyback prices are managed centrally via the <b>CH Ready Reckoner</b> in Item Master. " +
               "Price fields on this form are read-only."),
            "blue"
        );

        frm.add_custom_button(__("Open Ready Reckoner"), () => {
            frappe.set_route("ch-ready-reckoner");
        });
    },
});
