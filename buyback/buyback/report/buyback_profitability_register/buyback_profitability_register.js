// Buyback Profitability Register
frappe.query_reports["Buyback Profitability Register"] = {
    filters: buyback_filters.base_filters([
        buyback_filters.settlement_filter(),
        buyback_filters.brand_filter(),
        buyback_filters.item_group_filter(),
        {
            fieldname: "status",
            label: __("Status"),
            fieldtype: "Select",
            options: "\nDraft\nAwaiting Approval\nApproved\nAwaiting Customer Approval\nCustomer Approved\nAwaiting OTP\nOTP Verified\nReady to Pay\nPaid\nClosed\nRejected\nCancelled",
        },
        {
            fieldname: "sold_status",
            label: __("Sold Status"),
            fieldtype: "Select",
            options: "All\nSold\nUnsold",
            default: "All",
        },
    ]),
};
