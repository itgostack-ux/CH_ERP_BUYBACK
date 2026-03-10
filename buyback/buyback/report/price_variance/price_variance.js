// R7b — Price Variance
frappe.query_reports["Price Variance"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.brand_filter(),
		buyback_filters.item_group_filter(),
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
