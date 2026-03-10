// Copyright (c) 2026, GoStack and contributors
// Category Manager Dashboard — Brand/grade analytics and price trends

frappe.pages["category-manager-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Category Manager Dashboard"),
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
	page.brand = page.add_field({
		fieldname: "brand",
		label: __("Brand"),
		fieldtype: "Link",
		options: "Brand",
		change: () => refresh(page),
	});
	page.item_group = page.add_field({
		fieldname: "item_group",
		label: __("Item Group"),
		fieldtype: "Link",
		options: "Item Group",
		change: () => refresh(page),
	});

	page.content_area = $('<div class="category-dashboard-content"></div>').appendTo(page.main);
	refresh(page);
};

function refresh(page) {
	const filters = {
		from_date: page.fields_dict.from_date?.get_value(),
		to_date: page.fields_dict.to_date?.get_value(),
		brand: page.fields_dict.brand?.get_value(),
		item_group: page.fields_dict.item_group?.get_value(),
	};

	page.content_area.html('<div class="text-center text-muted p-5">Loading...</div>');

	frappe.call({
		method: "buyback.buyback.dashboard_api.get_category_dashboard",
		args: filters,
		callback: (r) => {
			if (r.message) render_category(page, r.message);
		},
	});
}

function render_category(page, data) {
	let html = `
		<div class="row mb-4">
			<div class="col-md-6"><div id="cat-brand-chart"></div></div>
			<div class="col-md-6"><div id="cat-grade-chart"></div></div>
		</div>
		<div class="row mb-4">
			<div class="col-md-12"><div id="cat-price-trend"></div></div>
		</div>
		<div class="row">
			<div class="col-md-12">
				<h6 class="text-muted mb-3">Top Depreciating Models</h6>
				<table class="table table-sm">
					<thead>
						<tr>
							<th>Model</th>
							<th>Brand</th>
							<th class="text-right">Avg Price (30d ago)</th>
							<th class="text-right">Avg Price (Now)</th>
							<th class="text-right">Depreciation %</th>
						</tr>
					</thead>
					<tbody>
						${(data.depreciation || []).map(d => `
							<tr>
								<td>${d.item || d.model || ""}</td>
								<td>${d.brand || ""}</td>
								<td class="text-right">${format_currency(d.old_price || 0)}</td>
								<td class="text-right">${format_currency(d.new_price || 0)}</td>
								<td class="text-right text-danger">${(d.depreciation_pct || 0).toFixed(1)}%</td>
							</tr>
						`).join("")}
					</tbody>
				</table>
			</div>
		</div>
	`;

	page.content_area.html(html);

	// Brand-wise bar chart
	if (data.brand_data && data.brand_data.length) {
		new frappe.Chart("#cat-brand-chart", {
			title: __("Brand-wise Quantity"),
			data: {
				labels: data.brand_data.map((d) => d.brand),
				datasets: [{ name: __("Qty"), values: data.brand_data.map((d) => d.qty) }],
			},
			type: "bar",
			height: 280,
			colors: ["#5e64ff"],
		});
	}

	// Grade distribution pie chart
	if (data.grade_data && data.grade_data.length) {
		new frappe.Chart("#cat-grade-chart", {
			title: __("Grade Distribution"),
			data: {
				labels: data.grade_data.map((d) => d.grade),
				datasets: [{ values: data.grade_data.map((d) => d.qty) }],
			},
			type: "pie",
			height: 280,
			colors: ["#29cd42", "#5e64ff", "#ffa00a", "#ff5858", "#7c5cfc"],
		});
	}

	// Monthly price trend line chart
	if (data.price_trend && data.price_trend.length) {
		new frappe.Chart("#cat-price-trend", {
			title: __("Monthly Price Trend"),
			data: {
				labels: data.price_trend.map((d) => d.month || d.date),
				datasets: [
					{ name: __("Avg Price"), values: data.price_trend.map((d) => d.avg_price), chartType: "line" },
				],
			},
			type: "line",
			height: 250,
			colors: ["#7c5cfc"],
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
