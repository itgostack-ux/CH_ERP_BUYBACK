// Copyright (c) 2026, Congruence Holdings and contributors
// For license information, please see license.txt

frappe.query_reports["Buyback Refurb Queue"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_default("company"),
			reqd: 0,
		},
		{
			fieldname: "min_age_days",
			label: __("Min Age (days since pickup / payout)"),
			fieldtype: "Int",
			default: 3,
			description: __(
				"Only surface orders older than N days — filters out the fresh, still-flowing queue."
			),
		},
		{
			fieldname: "only_missing_wipe",
			label: __("Only Missing Wipe Certificate"),
			fieldtype: "Check",
			default: 0,
			description: __(
				"When ON, hide orders that already have a submitted CH Data Wipe Certificate."
			),
		},
	],
	formatter: (value, row, column, data, default_formatter) => {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "has_data_wipe" && data) {
			if (!data.has_data_wipe) {
				value = `<span style="color:var(--red-500);font-weight:600">${__("Missing")}</span>`;
			} else {
				value = `<span style="color:var(--green-600);font-weight:600">${__("Yes")}</span>`;
			}
		}
		if (column.fieldname === "has_indemnity" && data) {
			if (!data.has_indemnity) {
				value = `<span style="color:var(--red-500);font-weight:600">${__("Missing")}</span>`;
			} else {
				value = `<span style="color:var(--green-600);font-weight:600">${__("Yes")}</span>`;
			}
		}
		if (column.fieldname === "linked_refurbishment" && data && !data.linked_refurbishment) {
			value = `<span style="color:var(--orange-500);font-weight:600">${__("Not Started")}</span>`;
		}
		return value;
	},
};
