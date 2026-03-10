// R6 — Model-wise Summary
frappe.query_reports["Model Wise Buyback"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.brand_filter(),
		buyback_filters.item_group_filter(),
		buyback_filters.settlement_filter(),
	]),
};
