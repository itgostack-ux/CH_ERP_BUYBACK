// Category Trend
frappe.query_reports["Category Trend"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.brand_filter(),
		buyback_filters.item_group_filter(),
	]),
};
