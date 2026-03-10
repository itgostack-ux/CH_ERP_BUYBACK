// R13 — Settlement Register
frappe.query_reports["Settlement Register"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.settlement_filter(),
		buyback_filters.brand_filter(),
	]),
};
