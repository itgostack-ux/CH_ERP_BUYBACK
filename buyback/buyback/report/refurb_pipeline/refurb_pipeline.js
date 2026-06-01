frappe.query_reports["Refurb Pipeline"] = {
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.month_start() },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.now_date() },
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "status", label: __("Status"), fieldtype: "Select", options: "\nReceived\nDiagnosed\nRepaired\nGraded\nRestocked\nCancelled" }
	],
};
