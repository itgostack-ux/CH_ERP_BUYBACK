// R18 — Daily Ops Queue
frappe.query_reports["Daily Ops Queue"] = {
	filters: [
		{
			fieldname: "store",
			label: __("Store / Branch"),
			fieldtype: "Link",
			options: "Warehouse",
			default: frappe.defaults.get_user_default("Warehouse"),
		},
	],
};
