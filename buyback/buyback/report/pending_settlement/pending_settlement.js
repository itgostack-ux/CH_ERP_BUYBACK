// R12 — Pending Settlement
frappe.query_reports["Pending Settlement"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.settlement_filter(),
	]),
};
