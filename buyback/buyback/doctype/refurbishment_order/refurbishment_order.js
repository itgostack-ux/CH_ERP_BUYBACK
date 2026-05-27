frappe.ui.form.on("Refurbishment Order", {
	refresh(frm) {
		if (frm.doc.status === "Restocked" && frm.doc.resulting_stock_entry) {
			frm.dashboard.set_headline_alert(__("Unit restocked via {0}", [frm.doc.resulting_stock_entry]), "green");
		}
	},
});
