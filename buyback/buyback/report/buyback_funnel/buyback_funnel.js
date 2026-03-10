// R1 — Unified Buyback Funnel
frappe.query_reports["Buyback Funnel"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.brand_filter(),
		buyback_filters.item_group_filter(),
		buyback_filters.source_filter(),
		buyback_filters.settlement_filter(),
	]),
};
