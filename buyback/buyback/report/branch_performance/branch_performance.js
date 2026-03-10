// R3 — Branch Performance
frappe.query_reports["Branch Performance"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.brand_filter(),
		buyback_filters.source_filter(),
	]),
};
