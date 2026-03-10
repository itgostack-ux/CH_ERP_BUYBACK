// R5 — Inspector Performance
frappe.query_reports["Inspector Scorecard"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.inspector_filter(),
	]),
};
