// Deduction Breakdown
frappe.query_reports["Deduction Breakdown"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.brand_filter(),
		buyback_filters.inspector_filter(),
	]),
};
