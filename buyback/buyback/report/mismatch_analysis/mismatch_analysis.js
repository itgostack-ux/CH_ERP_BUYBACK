// R9 — Mismatch Analysis
frappe.query_reports["Mismatch Analysis"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.brand_filter(),
		buyback_filters.inspector_filter(),
	]),
};
