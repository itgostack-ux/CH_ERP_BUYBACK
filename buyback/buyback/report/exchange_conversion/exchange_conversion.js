// R16 — Exchange Conversion
frappe.query_reports["Exchange Conversion"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.brand_filter(),
	]),
};
