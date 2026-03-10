// R7a — Quote Accuracy
frappe.query_reports["Quote Accuracy"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.brand_filter(),
		buyback_filters.source_filter(),
		buyback_filters.inspector_filter(),
		{
			fieldname: "variance_threshold",
			label: __("Variance Threshold (%)"),
			fieldtype: "Float",
			default: 10,
		},
	]),
};
