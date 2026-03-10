// Copyright (c) 2026, GoStack and contributors
// Finance Dashboard — Payment analytics and branch cash tracking

frappe.pages["finance-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Finance Dashboard"),
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

	page.content_area = $('<div class="finance-dashboard-content"></div>').appendTo(page.main);
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
		method: "buyback.buyback.dashboard_api.get_finance_dashboard",
		args: filters,
		callback: (r) => {
			if (r.message) render_finance(page, r.message);
		},
	});
}

function render_finance(page, data) {
	const k = data.kpis;

	let html = `
		<div class="row mb-4">
			${kpi_card("Total Paid", format_currency(k.total_paid), "green")}
			${kpi_card("Pending Count", k.pending_count, "orange")}
			${kpi_card("Pending Amount", format_currency(k.pending_amount), "orange")}
			${kpi_card("Payment Methods", k.payment_methods, "blue")}
		</div>
		<div class="row mb-4">
			<div class="col-md-5"><div id="fin-payment-method"></div></div>
			<div class="col-md-7"><div id="fin-daily-payouts"></div></div>
		</div>
		<div class="row">
			<div class="col-md-12">
				<h6 class="text-muted mb-3">Branch Cash Usage</h6>
				<table class="table table-sm">
					<thead>
						<tr>
							<th>Branch</th>
							<th class="text-right">Cash Payouts</th>
							<th class="text-right">Total Amount</th>
							<th class="text-right">Avg Per Order</th>
						</tr>
					</thead>
					<tbody>
						${(data.branch_cash || []).map(b => {
							const highlight = (b.total_amount || 0) > 200000 ? ' style="background-color: #fff3cd;"' : '';
							return `
								<tr${highlight}>
									<td>${b.branch || b.store || ""}</td>
									<td class="text-right">${b.count || 0}</td>
									<td class="text-right">${format_currency(b.total_amount || 0)}</td>
									<td class="text-right">${format_currency(b.avg_amount || 0)}</td>
								</tr>
							`;
						}).join("")}
					</tbody>
				</table>
			</div>
		</div>
	`;

	page.content_area.html(html);

	// Payment by method pie chart
	if (data.payment_by_method && data.payment_by_method.length) {
		new frappe.Chart("#fin-payment-method", {
			title: __("Payment by Method"),
			data: {
				labels: data.payment_by_method.map((d) => d.method || d.payment_method),
				datasets: [{ values: data.payment_by_method.map((d) => d.amount || d.count) }],
			},
			type: "pie",
			height: 280,
			colors: ["#29cd42", "#5e64ff", "#ffa00a", "#ff5858", "#7c5cfc"],
		});
	}

	// Daily payouts bar chart
	if (data.daily_payouts && data.daily_payouts.length) {
		new frappe.Chart("#fin-daily-payouts", {
			title: __("Daily Payouts"),
			data: {
				labels: data.daily_payouts.map((d) => d.date),
				datasets: [
					{ name: __("Amount"), values: data.daily_payouts.map((d) => d.amount), chartType: "bar" },
				],
			},
			type: "bar",
			height: 280,
			colors: ["#29cd42"],
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
