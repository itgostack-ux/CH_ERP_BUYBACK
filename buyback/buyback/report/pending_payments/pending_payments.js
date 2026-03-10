// Pending Payments
frappe.query_reports["Pending Payments"] = {
	filters: [
		{
			fieldname: "store",
			label: __("Store / Branch"),
			fieldtype: "Link",
			options: "Warehouse",
		},
	],
};
