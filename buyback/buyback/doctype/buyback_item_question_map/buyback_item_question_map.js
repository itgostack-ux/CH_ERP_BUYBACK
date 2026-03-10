// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Buyback Item Question Map", {
    setup(frm) {
        frm.set_query("question", "questions", () => ({
            filters: { diagnosis_type: "Customer Question", disabled: 0 },
        }));
        frm.set_query("test", "tests", () => ({
            filters: { diagnosis_type: "Automated Test", disabled: 0 },
        }));
    },

    map_type(frm) {
        if (frm.doc.map_type === "Subcategory") {
            // Clear model-level fields when switching to Subcategory
            frm.set_value("item_code", "");
            frm.set_value("item_name", "");
        }
    },

    item_code(frm) {
        // When model changes, auto-fetch subcategory
        if (frm.doc.item_code && frm.doc.map_type === "Model") {
            frappe.db.get_value("Item", frm.doc.item_code, "item_group", (r) => {
                if (r && r.item_group) {
                    frm.set_value("item_group", r.item_group);
                }
            });
        }
    },
});
