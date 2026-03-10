// R4 — Executive Performance
frappe.query_reports["Executive Performance"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.source_filter(),
	]),
};
