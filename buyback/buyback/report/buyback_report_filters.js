// Buyback Report — Standard Filter Definitions
// Import and extend in individual report JS files.
// Usage: frappe.query_reports["My Report"] = { filters: buyback_filters.base_filters() }

const buyback_filters = {
    // Base filters used by most reports
    base_filters: function(extra_filters) {
        let filters = [
            {
                fieldname: "company",
                label: __("Company"),
                fieldtype: "Link",
                options: "Company",
                default: frappe.defaults.get_user_default("Company"),
            },
            {
                fieldname: "from_date",
                label: __("From Date"),
                fieldtype: "Date",
                default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
                reqd: 1,
            },
            {
                fieldname: "to_date",
                label: __("To Date"),
                fieldtype: "Date",
                default: frappe.datetime.get_today(),
                reqd: 1,
            },
            {
                fieldname: "store",
                label: __("Store / Branch"),
                fieldtype: "Link",
                options: "Warehouse",
            },
        ];
        if (extra_filters) {
            filters = filters.concat(extra_filters);
        }
        return filters;
    },

    brand_filter: function() {
        return {
            fieldname: "brand",
            label: __("Brand"),
            fieldtype: "Link",
            options: "Brand",
        };
    },

    item_group_filter: function() {
        return {
            fieldname: "item_group",
            label: __("Category"),
            fieldtype: "Link",
            options: "Item Group",
        };
    },

    source_filter: function() {
        return {
            fieldname: "source",
            label: __("Source"),
            fieldtype: "Select",
            options: "\nMobile App\nIn-Store Kiosk\nStore Manual\nWebsite\nPartner API",
        };
    },

    settlement_filter: function() {
        return {
            fieldname: "settlement_type",
            label: __("Settlement Type"),
            fieldtype: "Select",
            options: "\nBuyback\nExchange",
        };
    },

    inspector_filter: function() {
        return {
            fieldname: "inspector",
            label: __("Inspector"),
            fieldtype: "Link",
            options: "User",
        };
    },
};
