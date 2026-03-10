// Copyright (c) 2026, GoStack and contributors
// Compliance Dashboard — Audit trail and fraud detection indicators

frappe.pages["compliance-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Compliance Dashboard"),
		single_column: true,
	});

	page.main.addClass("frappe-card");
	page.main.css("padding", "15px");
	wrapper.page = page;

	// Filters
	page.from_date = page.add_field({
		fieldname: "from_date",
		label: __("From"),
		fieldtype: "Date",
		default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
		change: () => refresh(page),
	});
	page.to_date = page.add_field({
		fieldname: "to_date",
		label: __("To"),
		fieldtype: "Date",
		default: frappe.datetime.get_today(),
		change: () => refresh(page),
	});
	page.company = page.add_field({
		fieldname: "company",
		label: __("Company"),
		fieldtype: "Link",
		options: "Company",
		default: frappe.defaults.get_user_default("Company"),
		change: () => refresh(page),
	});

	page.content_area = $('<div class="compliance-dashboard-content"></div>').appendTo(page.main);
	refresh(page);
};

function refresh(page) {
	const filters = {
		from_date: page.fields_dict.from_date?.get_value(),
		to_date: page.fields_dict.to_date?.get_value(),
		company: page.fields_dict.company?.get_value(),
	};

	page.content_area.html('<div class="text-center text-muted p-5">Loading...</div>');

	frappe.call({
		method: "buyback.buyback.dashboard_api.get_compliance_dashboard",
		args: filters,
		callback: (r) => {
			if (r.message) render_compliance(page, r.message);
		},
	});
}

function render_compliance(page, data) {
	const k = data.kpis;

	let html = `
		<div class="row mb-4">
			${kpi_card("Manager Overrides", k.manager_overrides, k.manager_overrides > 0 ? "orange" : "green")}
			${kpi_card("Duplicate IMEIs", k.duplicate_imeis, k.duplicate_imeis > 0 ? "red" : "green")}
			${kpi_card("High Value Orders", k.high_value_orders, "purple")}
			${kpi_card("High Value Total", format_currency(k.high_value_total), "purple")}
		</div>
		<div class="row mb-4">
			${kpi_card("Manual Approvals", k.manual_approvals, "orange")}
			${kpi_card("Auto Approvals", k.auto_approvals, "green")}
			${kpi_card("Large Payout Threshold", format_currency(k.large_payout_threshold), "blue")}
			<div class="col-md-3 col-sm-6 mb-3"></div>
		</div>
		<div class="row">
			<div class="col-md-12">
				<h6 class="text-muted mb-3">Recent Audit Actions</h6>
				<table class="table table-sm">
					<thead>
						<tr>
							<th>Date</th>
							<th>Action</th>
							<th>Reference</th>
							<th>User</th>
							<th>Reason</th>
						</tr>
					</thead>
					<tbody>
						${(data.recent_audits || []).map(a => `
							<tr>
								<td>${a.creation || a.date || ""}</td>
								<td><span class="badge badge-${audit_badge(a.action)}">${a.action || ""}</span></td>
								<td>${a.reference ? `<a href="/app/${(a.reference_type || "buyback-order").toLowerCase().replace(/ /g, "-")}/${a.reference}">${a.reference}</a>` : ""}</td>
								<td>${a.user || ""}</td>
								<td>${a.reason || ""}</td>
							</tr>
						`).join("")}
					</tbody>
				</table>
			</div>
		</div>
	`;

	page.content_area.html(html);
}

function audit_badge(action) {
	if (!action) return "secondary";
	const a = action.toLowerCase();
	if (a.includes("override") || a.includes("reject")) return "warning";
	if (a.includes("flag") || a.includes("duplicate") || a.includes("breach")) return "danger";
	if (a.includes("approve")) return "success";
	return "info";
}

function kpi_card(label, value, color) {
	const colors = { blue: "#5e64ff", green: "#29cd42", orange: "#ffa00a", red: "#ff5858", purple: "#7c5cfc" };
	return `
		<div class="col-md-3 col-sm-6 mb-3">
			<div class="border rounded p-3 h-100" style="border-left: 4px solid ${colors[color]} !important;">
				<div class="text-muted small">${label}</div>
				<div class="font-weight-bold h4 mb-0" style="color: ${colors[color]}">${value}</div>
			</div>
		</div>
	`;
}

function format_currency(val) {
	return "₹" + (parseFloat(val) || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}
