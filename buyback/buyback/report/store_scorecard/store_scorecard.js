// Store Scorecard
frappe.query_reports["Store Scorecard"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.brand_filter(),
	]),
};
