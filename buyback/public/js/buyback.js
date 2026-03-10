/**
 * Buyback Stepper Utility
 * Renders a visual progress bar at the top of transaction forms.
 *
 * Usage:
 *   buyback.stepper.render(frm, steps, current_status);
 */

frappe.provide("buyback.stepper");

buyback.stepper.FLOWS = {
    "Buyback Assessment": [
        "Draft", "Submitted", "Inspection Created", "Expired", "Cancelled"
    ],
    "Buyback Inspection": [
        "Draft", "In Progress", "Completed"
    ],
    "Buyback Order": [
        "Draft", "Awaiting Approval", "Approved",
        "Awaiting OTP", "OTP Verified",
        "Ready to Pay", "Paid", "Closed"
    ],
    "Buyback Exchange Order": [
        "Draft", "New Device Delivered", "Awaiting Pickup",
        "Old Device Received", "Inspected", "Settled", "Closed"
    ],
};

buyback.stepper.TERMINAL = ["Expired", "Rejected", "Cancelled"];

/**
 * Render a stepper bar in the form's header area.
 */
buyback.stepper.render = function (frm) {
    const dt = frm.doc.doctype;
    const steps = buyback.stepper.FLOWS[dt];
    if (!steps) return;

    // Remove previous stepper
    frm.$wrapper.find(".buyback-stepper").remove();

    const current = frm.doc.status || frm.doc.workflow_state || "Draft";
    const is_terminal = buyback.stepper.TERMINAL.includes(current);
    const current_idx = steps.indexOf(current);

    let html = '<div class="buyback-stepper">';
    steps.forEach((step, i) => {
        let cls = "";
        if (is_terminal && current === step) {
            cls = "rejected";
        } else if (i < current_idx) {
            cls = "completed";
        } else if (i === current_idx) {
            cls = "active";
        }

        if (i > 0) {
            const connector_cls = i <= current_idx ? "done" : "";
            html += `<div class="step-connector ${connector_cls}"></div>`;
        }

        const icon = cls === "completed"
            ? "✓"
            : cls === "rejected"
            ? "✗"
            : (i + 1);

        html += `<div class="step ${cls}">`;
        html += `<span class="step-circle">${icon}</span>`;
        html += `<span class="step-label">${step}</span>`;
        html += `</div>`;
    });

    // Show terminal status if not in main flow
    if (is_terminal && current_idx === -1) {
        html += `<div class="step-connector"></div>`;
        html += `<div class="step rejected">`;
        html += `<span class="step-circle">✗</span>`;
        html += `<span class="step-label">${current}</span>`;
        html += `</div>`;
    }

    html += "</div>";

    // Insert after form-header
    const $header = frm.$wrapper.find(".form-header");
    if ($header.length) {
        $header.after(html);
    }
};

// Auto-render stepper on supported DocTypes
$(document).on("form-refresh", function (e, frm) {
    if (buyback.stepper.FLOWS[frm.doc.doctype]) {
        buyback.stepper.render(frm);
    }
});

// ── Buyback Report — Standard Filter Definitions ──────────────────
// Used by all Script Report JS files via buyback_filters.base_filters()
const buyback_filters = {
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
