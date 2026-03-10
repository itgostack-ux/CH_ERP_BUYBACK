// Pending Confirmations
frappe.query_reports["Pending Confirmations"] = {
	filters: [
		{
			fieldname: "store",
			label: __("Store / Branch"),
			fieldtype: "Link",
			options: "Warehouse",
		},
	],
};
