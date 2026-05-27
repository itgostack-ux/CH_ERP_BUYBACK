frappe.ui.form.on("Store Credit Wallet", {
	refresh(frm) {
		frm.add_custom_button(__("Refresh Balance"), () => {
			frm.save();
		});
	},
});
