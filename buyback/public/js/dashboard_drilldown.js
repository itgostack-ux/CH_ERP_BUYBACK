// Open a buyback dashboard card onto the rows it counted.
//
// Shared by the Store Manager, Operations, Finance and Compliance dashboards.
// The card passes its own key; the server answers from BUYBACK_TILES, which is
// where each card's predicate is stated once. That matters more than it looks:
// three of the Store cards are not date-filtered, and Finance's pending-payout
// card counts the same statuses as Store's but IS date-filtered — so the list
// cannot be rebuilt on the client without getting one of them wrong.

frappe.provide("buyback.drilldown");

buyback.drilldown = {
	API: "buyback.buyback.dashboard_api.get_dashboard_tile_rows",

	/** Make every [data-bb-tile] card in $scope openable. */
	attach($scope, dashboard, get_args) {
		const self = this;
		$scope.find("[data-bb-tile]")
			.attr({ role: "button", tabindex: "0" })
			.addClass("bb-tile-open")
			.off("click.bbdd keypress.bbdd")
			.on("click.bbdd", function () {
				self.open($scope, dashboard, $(this).data("bb-tile"),
					$(this).find(".bb-tile-label,.text-muted").first().text(), get_args());
			})
			.on("keypress.bbdd", function (e) {
				if (e.key !== "Enter" && e.key !== " ") return;
				e.preventDefault();
				$(this).trigger("click.bbdd");
			});
	},

	open($scope, dashboard, tile, label, args) {
		const $panel = this._panel($scope);
		$panel.html(`<div class="text-muted" style="padding:16px">${__("Loading…")}</div>`);
		$scope.find("[data-bb-tile]").removeClass("is-open");
		$scope.find(`[data-bb-tile="${tile}"]`).addClass("is-open");

		frappe.xcall(this.API, Object.assign({ dashboard, tile }, args || {}))
			.then((r) => this._render($panel, r, label))
			.catch(() => {
				// Never strand the panel on "Loading…" — silence reads as a
				// slow query, not as a failure.
				$panel.html(
					`<div class="text-muted" style="padding:16px">${
						__("This card could not be opened. Refresh the page and try again.")
					}</div>`);
			});
	},

	_panel($scope) {
		let $p = $scope.find(".bb-drilldown");
		if (!$p.length) $p = $('<div class="bb-drilldown"></div>').appendTo($scope);
		return $p;
	},

	_render($panel, r, label) {
		const esc = frappe.utils.escape_html;
		if (!r || !r.rows || !r.rows.length) {
			$panel.html(`<div class="bb-drilldown-head">${esc(label || r.tile)}</div>
				<div class="text-muted" style="padding:12px 0">${__("Nothing in this card.")}</div>`);
			return;
		}
		const cols = r.columns;
		const head = cols.map((c) => `<th>${esc(frappe.model.unscrub(c))}</th>`).join("");
		const body = r.rows.map((row) => `<tr>${cols.map((c, i) => {
			const v = row[c];
			const cell = v === null || v === undefined ? "" : String(v);
			return i === 0
				? `<td><a href="/app/${frappe.router.slug(r.doctype)}/${encodeURIComponent(cell)}">${esc(cell)}</a></td>`
				: `<td>${esc(cell)}</td>`;
		}).join("")}</tr>`).join("");

		$panel.html(`
			<div class="bb-drilldown-head">
				${esc(label || r.tile)}
				<span class="text-muted">— ${__("{0} row(s)", [r.total])}${
					r.truncated ? __(", first {0} shown", [r.total]) : ""}</span>
			</div>
			<div class="bb-drilldown-scroll">
				<table class="table table-sm"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
			</div>
		`);
	},
};
