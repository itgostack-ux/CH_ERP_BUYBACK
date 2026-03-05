/**
 * Buyback Stepper Utility
 * Renders a visual progress bar at the top of transaction forms.
 *
 * Usage:
 *   buyback.stepper.render(frm, steps, current_status);
 */

frappe.provide("buyback.stepper");

buyback.stepper.FLOWS = {
    "Buyback Quote": [
        "Draft", "Quoted", "Accepted", "Expired"
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
