frappe.pages["buyback-hub"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Buyback Hub"),
		single_column: true,
	});
	wrapper.buyback_hub = new BuybackHub(page);
};

frappe.pages["buyback-hub"].refresh = function (wrapper) {
	wrapper.buyback_hub && wrapper.buyback_hub.refresh();
};

class BuybackHub {
	constructor(page) {
		this.page = page;
		this._timer = null;
		this._setup_controls();
		this._setup_container();
		this.refresh();
		this._start_auto_refresh();
	}

	_setup_controls() {
		this.company_field = this.page.add_field({
			fieldname: "company", label: __("Company"),
			fieldtype: "Link", options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			change: () => this.refresh(),
		});
		this.store_field = this.page.add_field({
			fieldname: "store", label: __("Store / Warehouse"),
			fieldtype: "Link", options: "Warehouse",
			get_query: () => {
				const company = this.company_field?.get_value();
				const filters = { is_group: 0 };
				if (company) filters.company = company;
				return { filters };
			},
			change: () => this.refresh(),
		});
		this.from_date_field = this.page.add_field({
			fieldname: "from_date", label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			change: () => this.refresh(),
		});
		this.to_date_field = this.page.add_field({
			fieldname: "to_date", label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			change: () => this.refresh(),
		});
		this.page.add_button(__("Refresh"), () => this.refresh(), { icon: "refresh" });
	}

	_setup_container() {
		this.$root = $(`<div class="hub-root"></div>`).appendTo(this.page.body);
	}

	_go_list(doctype, filters = {}) {
		const co = this.company_field?.get_value();
		if (co) filters.company = co;
		frappe.set_route("List", doctype, filters);
	}

	refresh() {
		const company = this.company_field?.get_value() || "";
		const store = this.store_field?.get_value() || "";
		const from_date = this.from_date_field?.get_value() || "";
		const to_date = this.to_date_field?.get_value() || "";
		this.$root.html(`<div class="hub-loading"><i class="fa fa-spinner fa-spin"></i> ${__("Loading Buyback Hub...")}</div>`);
		frappe.xcall("buyback.buyback.page.buyback_hub.buyback_hub_api.get_buyback_hub_data",
			{ company, store, from_date, to_date })
			.then((data) => this._render(data))
			.catch(() => {
				this.$root.html(`<div class="hub-loading text-danger">${__("Failed to load data. Please try again.")}</div>`);
			});
	}

	_start_auto_refresh() {
		this._timer = setInterval(() => this.refresh(), 60000);
		$(this.page.parent).on("remove", () => clearInterval(this._timer));
	}

	_render(data) {
		this.$root.empty();
		this._render_header();
		this._render_pipeline(data.pipeline || []);
		this._render_kpis(data.kpis || []);
		this._render_actions();
		this._render_intelligence(data.ai_insights || [], data.financial_control || {});
		this._render_tables(data);
	}

	_render_header() {
		this.$root.append(`
			<div class="hub-header">
				<div>
					<div class="hub-title"><i class="fa fa-exchange"></i> ${__("Buyback & Exchange Hub")}</div>
					<div class="hub-subtitle">${__("Buyback lifecycle: Assessment → OTP → Approval → Inspection → Payment → Closed")}</div>
				</div>
				<div class="hub-auto-badge">
					<span class="pulse-dot"></span> ${__("Live · Auto-refreshes every 60s")}
				</div>
			</div>
		`);
	}

	_render_pipeline(steps) {
		const arrow = `<div class="hub-flow-connector">
			<svg width="32" height="24" viewBox="0 0 32 24" fill="none" stroke="currentColor"
				stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
				<path d="M4 12H24M18 6l6 6-6 6"/>
			</svg>
		</div>`;
		const nodes = steps.map((s, i) => {
			const node = `
				<div class="hub-flow-node" data-step="${s.key}">
					<div class="hub-flow-badge" style="background:${s.color}">${s.count}</div>
					<div class="hub-flow-meta">
						<i class="fa fa-${s.icon}"></i>
						<span class="hub-flow-name">${__(s.label)}</span>
					</div>
					<div class="hub-flow-sub">${s.sub || ""}</div>
				</div>`;
			return i < steps.length - 1 ? node + arrow : node;
		}).join("");
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-random"></i> ${__("Buyback Pipeline")}</h5>
				<div class="hub-flow-wrap"><div class="hub-flow">${nodes}</div></div>
			</div>
		`);
	}

	_render_kpis(kpis) {
		const cards = kpis.map((k) => {
			const val = k.fmt === "currency"
				? frappe.format(k.value, { fieldtype: "Currency" })
				: k.value;
			return `<div class="hub-kpi-card" style="--kpi-color:${k.color}" data-kpi="${k.key}">
				<div class="hub-kpi-value">${val}</div>
				<div class="hub-kpi-label">${__(k.label)}</div>
			</div>`;
		}).join("");
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-tachometer"></i> ${__("Key Metrics")}</h5>
				<div class="hub-kpi-grid">${cards}</div>
			</div>
		`);
	}

	_render_actions() {
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-bolt"></i> ${__("Quick Actions")}</h5>
				<div class="hub-actions-grid">
					<button class="hub-action-btn" data-act="new_orders"><i class="fa fa-plus"></i> ${__("New Orders")}</button>
					<button class="hub-action-btn" data-act="awaiting_otp"><i class="fa fa-mobile"></i> ${__("Awaiting OTP")}</button>
					<button class="hub-action-btn" data-act="approved"><i class="fa fa-check"></i> ${__("Approved")}</button>
					<button class="hub-action-btn" data-act="assessments"><i class="fa fa-search"></i> ${__("Assessments")}</button>
					<button class="hub-action-btn" data-act="inspections"><i class="fa fa-clipboard"></i> ${__("Inspections")}</button>
					<button class="hub-action-btn" data-act="ops_dash"><i class="fa fa-dashboard"></i> ${__("Ops Dashboard")}</button>
				</div>
			</div>
		`);

		this.$root.on("click", ".hub-action-btn", (e) => {
			const actions = {
				new_orders:   () => this._go_list("Buyback Order", { status: "Draft" }),
				awaiting_otp: () => this._go_list("Buyback Order", { status: "Awaiting OTP" }),
				approved:     () => this._go_list("Buyback Order", { status: "Approved" }),
				assessments:  () => this._go_list("Buyback Assessment"),
				inspections:  () => this._go_list("Buyback Inspection"),
				ops_dash:     () => frappe.set_route("app", "operations-dashboard"),
			};
			const fn = actions[$(e.currentTarget).data("act")];
			if (fn) fn();
		});
	}

	_render_intelligence(insights, financial) {
		const insightCards = insights.map((i) => `
			<div class="hub-insight-card hub-insight-${(i.severity || 'medium').toLowerCase()}">
				<div class="hub-insight-top">
					<span class="hub-badge hub-badge-${i.severity === 'High' ? 'red' : i.severity === 'Low' ? 'green' : 'yellow'}">${i.severity}</span>
					<span class="hub-insight-title">${i.title}</span>
				</div>
				<div class="hub-insight-detail">${i.detail}</div>
				${i.action ? `<div class="hub-insight-action">${i.action}</div>` : ""}
			</div>
		`).join("");

		const fc = financial;
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-brain"></i> ${__("AI Insights & Financial Control")}</h5>
				<div class="hub-intel-grid">
					<div class="hub-intel-panel">${insightCards || '<div class="hub-empty">No insights</div>'}</div>
					<div class="hub-intel-panel">
						<div class="hub-mini-kpi-grid">
							<div class="hub-mini-kpi" style="--mini-color:#ea580c">
								<div class="hub-mini-kpi-value">${frappe.format(fc.total_buyback_value || 0, {fieldtype:"Currency"})}</div>
								<div class="hub-mini-kpi-label">${__("Total Buyback Value")}</div>
							</div>
							<div class="hub-mini-kpi" style="--mini-color:#059669">
								<div class="hub-mini-kpi-value">${fc.approval_rate || "0%"}</div>
								<div class="hub-mini-kpi-label">${__("Approval Rate")}</div>
							</div>
							<div class="hub-mini-kpi" style="--mini-color:#3b82f6">
								<div class="hub-mini-kpi-value">${frappe.format(fc.avg_order_value || 0, {fieldtype:"Currency"})}</div>
								<div class="hub-mini-kpi-label">${__("Avg Order Value")}</div>
							</div>
							<div class="hub-mini-kpi" style="--mini-color:#ef4444">
								<div class="hub-mini-kpi-value">${fc.rejection_rate || "0%"}</div>
								<div class="hub-mini-kpi-label">${__("Rejection Rate")}</div>
							</div>
						</div>
					</div>
				</div>
			</div>
		`);
	}

	_render_tables(data) {
		const tabs = [
			{ key: "orders", label: __("Recent Orders"), count: (data.recent_orders || []).length },
			{ key: "pending", label: __("Pending Action"), count: (data.pending_action || []).length },
			{ key: "assessments", label: __("Assessments"), count: (data.recent_assessments || []).length },
			{ key: "inspections", label: __("Inspections"), count: (data.recent_inspections || []).length },
			{ key: "brands", label: __("Brand Summary"), count: (data.brand_summary || []).length },
		];
		const tabBtns = tabs.map((t, i) =>
			`<button class="hub-tab${i === 0 ? " active" : ""}" data-tab="${t.key}">
				${t.label} <span class="badge">${t.count}</span>
			</button>`
		).join("");

		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-table"></i> ${__("Detail Tables")}</h5>
				<div class="hub-tabs">${tabBtns}</div>
				<div class="hub-tab-panel active" data-panel="orders">${this._table_orders(data.recent_orders || [])}</div>
				<div class="hub-tab-panel" data-panel="pending">${this._table_pending(data.pending_action || [])}</div>
				<div class="hub-tab-panel" data-panel="assessments">${this._table_assessments(data.recent_assessments || [])}</div>
				<div class="hub-tab-panel" data-panel="inspections">${this._table_inspections(data.recent_inspections || [])}</div>
				<div class="hub-tab-panel" data-panel="brands">${this._table_brands(data.brand_summary || [])}</div>
			</div>
		`);

		this.$root.find(".hub-tab").on("click", (e) => {
			const key = $(e.currentTarget).data("tab");
			this.$root.find(".hub-tab").removeClass("active");
			$(e.currentTarget).addClass("active");
			this.$root.find(".hub-tab-panel").removeClass("active");
			this.$root.find(`[data-panel="${key}"]`).addClass("active");
		});
	}

	_lnk(dt, name) { return `<a href="/app/${frappe.router.slug(dt)}/${name}">${name}</a>`; }
	_badge(status) {
		const map = { "Draft": "grey", "Awaiting OTP": "yellow", "Awaiting Customer Approval": "yellow", "Approved": "green", "Rejected": "red", "Paid": "blue", "Closed": "grey", "Cancelled": "grey" };
		return `<span class="hub-badge hub-badge-${map[status] || "grey"}">${status}</span>`;
	}

	_table_orders(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-exchange"></i> ${__("No recent orders")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Order")}</th><th>${__("Customer")}</th><th>${__("Device")}</th>
			<th>${__("Status")}</th><th class="text-right">${__("Value")}</th><th>${__("Date")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${this._lnk("Buyback Order", r.name)}</td>
			<td>${r.customer_name || r.customer || ""}</td>
			<td>${r.device_name || r.item_name || ""}</td>
			<td>${this._badge(r.status)}</td>
			<td class="text-right">${frappe.format(r.buyback_value || 0, {fieldtype:"Currency"})}</td>
			<td>${frappe.datetime.str_to_user(r.creation)}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}

	_table_pending(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-check-circle"></i> ${__("No pending actions")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Order")}</th><th>${__("Customer")}</th><th>${__("Status")}</th>
			<th>${__("Pending Since")}</th><th>${__("Days")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${this._lnk("Buyback Order", r.name)}</td>
			<td>${r.customer_name || r.customer || ""}</td>
			<td>${this._badge(r.status)}</td>
			<td>${frappe.datetime.str_to_user(r.modified)}</td>
			<td>${r.days_pending || "-"}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}

	_table_assessments(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-search"></i> ${__("No recent assessments")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Assessment")}</th><th>${__("Customer")}</th><th>${__("Device")}</th>
			<th>${__("Grade")}</th><th>${__("Est. Price")}</th><th>${__("Date")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${this._lnk("Buyback Assessment", r.name)}</td>
			<td>${r.customer_name || ""}</td>
			<td>${r.item_name || ""}</td>
			<td><span class="hub-badge hub-badge-blue">${r.grade || "-"}</span></td>
			<td class="text-right">${frappe.format(r.estimated_price || 0, {fieldtype:"Currency"})}</td>
			<td>${frappe.datetime.str_to_user(r.creation)}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}

	_table_inspections(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-clipboard"></i> ${__("No recent inspections")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Inspection")}</th><th>${__("Assessment")}</th><th>${__("Grade")}</th>
			<th>${__("Status")}</th><th>${__("Date")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${this._lnk("Buyback Inspection", r.name)}</td>
			<td>${r.buyback_assessment || ""}</td>
			<td><span class="hub-badge hub-badge-blue">${r.condition_grade || "-"}</span></td>
			<td>${this._badge(r.status)}</td>
			<td>${frappe.datetime.str_to_user(r.creation)}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}

	_table_brands(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-tag"></i> ${__("No brand data")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Brand")}</th><th class="text-right">${__("Orders")}</th>
			<th class="text-right">${__("Approved")}</th><th class="text-right">${__("Rejected")}</th>
			<th class="text-right">${__("Total Value")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${r.brand || "N/A"}</td>
			<td class="text-right">${r.total || 0}</td>
			<td class="text-right">${r.approved || 0}</td>
			<td class="text-right">${r.rejected || 0}</td>
			<td class="text-right">${frappe.format(r.total_value || 0, {fieldtype:"Currency"})}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}
}
