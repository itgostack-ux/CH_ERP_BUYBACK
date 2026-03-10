// R17 — SLA Breach Report
frappe.query_reports["SLA Breach Report"] = {
	filters: buyback_filters.base_filters([
		{
			fieldname: "sla_stage",
			label: __("SLA Stage"),
			fieldtype: "Select",
			options: "\nQuote to Inspection\nInspection to Approval\nApproval to Settlement\nSettlement to Stock Entry",
		},
	]),
};
