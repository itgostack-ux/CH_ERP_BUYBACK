// Copyright (c) 2026, GoStack and contributors
// Operations Dashboard — SLA tracking and pipeline visibility

frappe.pages["operations-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Operations Dashboard"),
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
	page.store = page.add_field({
		fieldname: "store",
		label: __("Store"),
		fieldtype: "Link",
		options: "Warehouse",
		change: () => refresh(page),
	});

	page.content_area = $('<div class="operations-dashboard-content"></div>').appendTo(page.main);
	refresh(page);
};

function refresh(page) {
	const filters = {
		from_date: page.fields_dict.from_date?.get_value(),
		to_date: page.fields_dict.to_date?.get_value(),
		store: page.fields_dict.store?.get_value(),
	};

	page.content_area.html('<div class="text-center text-muted p-5">Loading...</div>');

	frappe.call({
		method: "buyback.buyback.dashboard_api.get_operations_dashboard",
		args: filters,
		callback: (r) => {
			if (r.message) render_operations(page, r.message);
		},
	});
}

function render_operations(page, data) {
	const k = data.kpis;

	const compliance_color = k.sla_compliance >= 90 ? "green" : k.sla_compliance >= 70 ? "orange" : "red";

	let html = `
		<div class="row mb-4">
			${kpi_card("SLA On Time", k.sla_on_time, "green")}
			${kpi_card("Warnings", k.sla_warnings, "orange")}
			${kpi_card("Breaches", k.sla_breaches, k.sla_breaches > 0 ? "red" : "green")}
			${kpi_card("Compliance", k.sla_compliance + "%", compliance_color)}
		</div>
		<div class="row mb-4">
			<div class="col-md-4"><div id="ops-inspection-pipeline"></div></div>
			<div class="col-md-4"><div id="ops-exchange-pipeline"></div></div>
			<div class="col-md-4"><div id="ops-hourly-volume"></div></div>
		</div>
	`;

	page.content_area.html(html);

	// Inspection pipeline pie chart
	if (data.inspection_pipeline && data.inspection_pipeline.length) {
		new frappe.Chart("#ops-inspection-pipeline", {
			title: __("Inspection Pipeline"),
			data: {
				labels: data.inspection_pipeline.map((d) => d.status),
				datasets: [{ values: data.inspection_pipeline.map((d) => d.count) }],
			},
			type: "pie",
			height: 280,
			colors: ["#29cd42", "#5e64ff", "#ffa00a", "#ff5858", "#7c5cfc", "#36c6d3"],
		});
	}

	// Exchange pipeline bar chart
	if (data.exchange_pipeline && data.exchange_pipeline.length) {
		new frappe.Chart("#ops-exchange-pipeline", {
			title: __("Exchange Pipeline"),
			data: {
				labels: data.exchange_pipeline.map((d) => d.status),
				datasets: [
					{ name: __("Count"), values: data.exchange_pipeline.map((d) => d.count) },
				],
			},
			type: "bar",
			height: 280,
			colors: ["#7c5cfc"],
		});
	}

	// Hourly volume bar chart
	if (data.hourly_volume && data.hourly_volume.length) {
		new frappe.Chart("#ops-hourly-volume", {
			title: __("Hourly Volume"),
			data: {
				labels: data.hourly_volume.map((d) => d.hour),
				datasets: [
					{ name: __("Orders"), values: data.hourly_volume.map((d) => d.count) },
				],
			},
			type: "bar",
			height: 280,
			colors: ["#5e64ff"],
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
