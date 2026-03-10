// R2 — Source Mix
frappe.query_reports["Source Mix"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.brand_filter(),
		buyback_filters.item_group_filter(),
		{
			fieldname: "period",
			label: __("Group By"),
			fieldtype: "Select",
			options: "Day\nWeek\nMonth",
			default: "Day",
		},
	]),
};
