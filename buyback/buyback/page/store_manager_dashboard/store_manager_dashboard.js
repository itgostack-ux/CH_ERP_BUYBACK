// Copyright (c) 2026, GoStack and contributors
// Store Manager Dashboard — Store-level buyback KPIs

frappe.pages["store-manager-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Store Manager Dashboard"),
		single_column: true,
	});

	page.main.addClass("frappe-card");
	page.main.css("padding", "15px");
	wrapper.page = page;

	// Filters
	page.store = page.add_field({
		fieldname: "store",
		label: __("Store"),
		fieldtype: "Link",
		options: "Warehouse",
		reqd: 1,
		get_query: () => ({ filters: { ch_is_buyback_enabled: 1 } }),
		change: () => refresh(page),
	});
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

	page.content_area = $('<div class="store-dashboard-content"></div>').appendTo(page.main);
	// Wait for store selection before first load
};

function refresh(page) {
	const store = page.fields_dict.store?.get_value();
	if (!store) {
		page.content_area.html('<div class="text-center text-muted p-5">Please select a store</div>');
		return;
	}

	const filters = {
		store: store,
		from_date: page.fields_dict.from_date?.get_value(),
		to_date: page.fields_dict.to_date?.get_value(),
	};

	page.content_area.html('<div class="text-center text-muted p-5">Loading...</div>');

	frappe.call({
		method: "buyback.buyback.dashboard_api.get_store_dashboard",
		args: filters,
		callback: (r) => {
			if (r.message) render_store(page, r.message);
		},
	});
}

function render_store(page, data) {
	const k = data.kpis;

	const sla_color = k.sla_compliance >= 90 ? "green" : k.sla_compliance >= 70 ? "orange" : "red";

	let html = `
		<div class="row mb-4">
			${kpi_card("Total Orders", k.total_orders, "blue")}
			${kpi_card("Paid", k.paid, "green")}
			${kpi_card("Total Payout", format_currency(k.total_payout), "green")}
			${kpi_card("Pending", k.pending, "orange")}
		</div>
		<div class="row mb-4">
			${kpi_card("SLA Compliance", k.sla_compliance + "%", sla_color)}
			${kpi_card("SLA Breaches", k.sla_breaches, k.sla_breaches > 0 ? "red" : "green")}
			${kpi_card("Pending Approvals", k.pending_approvals, k.pending_approvals > 0 ? "orange" : "green")}
			${kpi_card("Pending Payments", k.pending_payments, k.pending_payments > 0 ? "orange" : "green")}
		</div>
		<div class="row">
			<div class="col-md-8">
				<h6 class="text-muted mb-3">Top Models</h6>
				<table class="table table-sm">
					<thead><tr><th>Model</th><th class="text-right">Qty</th><th class="text-right">Value</th></tr></thead>
					<tbody>
						${(data.top_models || []).map(m => `
							<tr>
								<td>${m.item || m.model || ""}</td>
								<td class="text-right">${m.qty || 0}</td>
								<td class="text-right">${format_currency(m.value || 0)}</td>
							</tr>
						`).join("")}
					</tbody>
				</table>
			</div>
			<div class="col-md-4">
				<div class="border rounded p-3 h-100">
					<h6 class="text-muted mb-3">Pending Pickups</h6>
					<div class="h2 text-center font-weight-bold" style="color: ${k.pending_pickups > 0 ? '#ffa00a' : '#29cd42'}">
						${k.pending_pickups || 0}
					</div>
					<div class="text-center text-muted small">devices awaiting pickup</div>
				</div>
			</div>
		</div>
	`;

	page.content_area.html(html);
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
