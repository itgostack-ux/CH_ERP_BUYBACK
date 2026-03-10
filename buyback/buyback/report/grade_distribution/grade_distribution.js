// R8 — Grade Variance
frappe.query_reports["Grade Distribution"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.brand_filter(),
		buyback_filters.inspector_filter(),
	]),
};
