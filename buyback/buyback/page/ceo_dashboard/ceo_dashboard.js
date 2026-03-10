// Copyright (c) 2026, GoStack and contributors
// CEO Dashboard — High-level buyback KPIs

frappe.pages["ceo-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("CEO Buyback Dashboard"),
		single_column: true,
	});

	page.main.addClass("frappe-card");
	page.main.css("padding", "15px");
	wrapper.page = page;

	// Filters
	page.company = page.add_field({
		fieldname: "company",
		label: __("Company"),
		fieldtype: "Link",
		options: "Company",
		default: frappe.defaults.get_user_default("Company"),
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

	page.content_area = $('<div class="ceo-dashboard-content"></div>').appendTo(page.main);
	refresh(page);
};

function refresh(page) {
	const filters = {
		company: page.fields_dict.company?.get_value(),
		from_date: page.fields_dict.from_date?.get_value(),
		to_date: page.fields_dict.to_date?.get_value(),
	};

	page.content_area.html('<div class="text-center text-muted p-5">Loading...</div>');

	frappe.call({
		method: "buyback.buyback.dashboard_api.get_ceo_dashboard",
		args: filters,
		callback: (r) => {
			if (r.message) render_ceo(page, r.message);
		},
	});
}

function render_ceo(page, data) {
	const k = data.kpis;
	let html = `
		<div class="row mb-4">
			${kpi_card("Total Quotes", k.total_quotes, "blue")}
			${kpi_card("Total Orders", k.total_orders, "blue")}
			${kpi_card("Paid Orders", k.paid_orders, "green")}
			${kpi_card("Total Payout", format_currency(k.total_payout), "green")}
		</div>
		<div class="row mb-4">
			${kpi_card("Conversion Rate", k.conversion_rate + "%", k.conversion_rate >= 60 ? "green" : "orange")}
			${kpi_card("Avg Order Value", format_currency(k.avg_order_value), "blue")}
			${kpi_card("Exchanges", k.total_exchanges, "purple")}
			${kpi_card("Rejection Rate", k.rejection_rate + "%", k.rejection_rate > 15 ? "red" : "green")}
		</div>
		<div class="row">
			<div class="col-md-8"><div id="ceo-daily-trend"></div></div>
			<div class="col-md-4">
				<h6 class="text-muted mb-3">Top Branches</h6>
				<table class="table table-sm">
					<thead><tr><th>Branch</th><th class="text-right">Orders</th><th class="text-right">Payout</th></tr></thead>
					<tbody>
						${(data.top_branches || []).map(b => `
							<tr>
								<td><a href="/app/warehouse/${b.store}">${b.store}</a></td>
								<td class="text-right">${b.orders}</td>
								<td class="text-right">${format_currency(b.payout)}</td>
							</tr>
						`).join("")}
					</tbody>
				</table>
				<h6 class="text-muted mb-3 mt-4">Top Models</h6>
				<table class="table table-sm">
					<thead><tr><th>Model</th><th class="text-right">Qty</th><th class="text-right">Value</th></tr></thead>
					<tbody>
						${(data.top_models || []).map(m => `
							<tr>
								<td>${m.item}</td>
								<td class="text-right">${m.qty}</td>
								<td class="text-right">${format_currency(m.value)}</td>
							</tr>
						`).join("")}
					</tbody>
				</table>
			</div>
		</div>
	`;

	page.content_area.html(html);

	// Render daily trend chart
	if (data.daily_trend && data.daily_trend.length) {
		new frappe.Chart("#ceo-daily-trend", {
			title: __("Daily Buyback Activity"),
			data: {
				labels: data.daily_trend.map((d) => d.date),
				datasets: [
					{ name: __("Orders"), values: data.daily_trend.map((d) => d.orders), chartType: "bar" },
					{ name: __("Payout (₹)"), values: data.daily_trend.map((d) => d.payout), chartType: "line" },
				],
			},
			type: "axis-mixed",
			height: 300,
			colors: ["#5e64ff", "#29cd42"],
		});
	}
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
