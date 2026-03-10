// Finance Payout Register
frappe.query_reports["Finance Payout Register"] = {
	filters: buyback_filters.base_filters([
		buyback_filters.settlement_filter(),
	]),
};
