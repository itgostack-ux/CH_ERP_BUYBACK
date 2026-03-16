frappe.ui.form.on("Buyback Assessment", {
	refresh(frm) {
		// Auto-fill store from POS session and make it read-only
		if (frm.is_new() && frappe.route_options && frappe.route_options._from_pos) {
			const pos_store = frappe.route_options.store;
			if (pos_store && !frm.doc.store) {
				frm.set_value("store", pos_store);
			}
		}
		// Lock store field if opened from POS (or if already saved with a store)
		const from_pos = frappe.route_options && frappe.route_options._from_pos;
		if (from_pos || (!frm.is_new() && frm.doc.store)) {
			frm.set_df_property("store", "read_only", 1);
		}

		// "New Customer" quick-entry button (always visible in Draft / unsaved)
		if (!frm.doc.docstatus && (!frm.doc.status || frm.doc.status === "Draft")) {
			frm.add_custom_button(__("New Customer"), () => {
				frappe.prompt([
					{ fieldname: "customer_name", fieldtype: "Data", label: __("Customer Name"), reqd: 1 },
					{ fieldname: "mobile_no", fieldtype: "Data", label: __("Mobile No"), reqd: 1 },
					{ fieldname: "email_id", fieldtype: "Data", label: __("Email (optional)") },
				], (values) => {
					frappe.call({
						method: "frappe.client.insert",
						args: {
							doc: {
								doctype: "Customer",
								customer_name: values.customer_name,
								customer_type: "Individual",
								customer_group: "Individual",
								territory: "India",
								mobile_no: values.mobile_no,
								email_id: values.email_id || "",
							},
						},
						callback(r) {
							if (r.message) {
								frm.set_value("customer", r.message.name);
								if (!frm.doc.mobile_no) {
									frm.set_value("mobile_no", values.mobile_no);
								}
								frappe.show_alert({
									message: __("Customer {0} created", [r.message.customer_name]),
									indicator: "green",
								});
							}
						},
					});
				}, __("Create New Customer"), __("Create"));
			});
		}

		// Hide "Mobile App" from Source dropdown — only settable via API
		if (frm.doc.source !== "Mobile App") {
			frm.set_df_property("source", "options",
				["In-Store Kiosk", "Web", "Store Manual"]
			);
		}

		// Stepper
		if (typeof buyback_render_stepper === "function") {
			const steps = [
				{ label: "Draft", status: "Draft" },
				{ label: "Submitted", status: "Submitted" },
				{ label: "Inspection Created", status: "Inspection Created" },
			];
			buyback_render_stepper(frm, steps);
		}

		// Action buttons
		const has_data = (frm.doc.responses && frm.doc.responses.length)
			|| (frm.doc.diagnostic_tests && frm.doc.diagnostic_tests.length);

		if (frm.doc.status === "Draft" && has_data) {
			frm.add_custom_button(__("Submit Assessment"), () => {
				frappe.call({
					method: "buyback.api.submit_assessment",
					args: { assessment_name: frm.doc.name },
					freeze: true,
					freeze_message: __("Submitting assessment..."),
					callback(r) {
						frm.reload_doc();
					},
				});
			}, __("Actions")).addClass("btn-primary-dark");
		}

		if (frm.doc.status === "Submitted") {
			frm.add_custom_button(__("Create Inspection"), () => {
				frappe.call({
					method: "buyback.api.create_inspection_from_assessment",
					args: { assessment_name: frm.doc.name },
					freeze: true,
					freeze_message: __("Creating inspection..."),
					callback(r) {
						if (r.message) {
							frappe.set_route("Form", "Buyback Inspection", r.message.name);
						}
					},
				});
			}, __("Actions")).addClass("btn-primary");
		}

		if (frm.doc.status === "Draft") {
			frm.add_custom_button(__("Calculate Estimate"), () => {
				frm.save().then(() => {
					frm.reload_doc();
					frappe.show_alert({
						message: __("Estimate calculated: ₹{0}", [frm.doc.estimated_price]),
						indicator: "green",
					});
				});
			}, __("Actions"));
		}

		// Show link to created inspection
		if (frm.doc.buyback_inspection) {
			frm.dashboard.add_comment(
				__("Inspection created: {0}", [
					`<a href="/app/buyback-inspection/${frm.doc.buyback_inspection}">${frm.doc.buyback_inspection}</a>`,
				]),
				"blue",
				true
			);
		}

		// Filter diagnostic_tests Link to only show Automated Test type
		frm.set_query("test", "diagnostic_tests", () => ({
			filters: { diagnosis_type: "Automated Test", disabled: 0 },
		}));

		// Filter responses Link to only show Customer Question type
		frm.set_query("question", "responses", () => ({
			filters: { diagnosis_type: "Customer Question", disabled: 0 },
		}));

		// Render reference price cards
		buyback_render_price_cards(frm);

		// Auto-load tests/questions on refresh if item is set but tables are empty
		if (frm.doc.item && frm.doc.status === "Draft") {
			buyback_load_diagnostic_tests(frm);
			buyback_load_customer_questions(frm);
		}
	},

	item(frm) {
		// Force reload when item changes (clear guard)
		frm.clear_table("diagnostic_tests");
		frm.clear_table("responses");
		frm.refresh_field("diagnostic_tests");
		frm.refresh_field("responses");

		buyback_render_price_cards(frm);
		buyback_load_diagnostic_tests(frm);
		buyback_load_customer_questions(frm);
	},
	device_age_months(frm) { buyback_render_price_cards(frm); buyback_recalculate_estimate(frm); },
	warranty_status(frm) { buyback_render_price_cards(frm); buyback_recalculate_estimate(frm); },
});

// ── Customer Question Responses: populate answer dropdown inline ──
frappe.ui.form.on("Buyback Assessment Response", {
	question(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.question) return;

		// Clear previous answer
		frappe.model.set_value(cdt, cdn, "answer_value", "");
		frappe.model.set_value(cdt, cdn, "answer_label", "");
		frappe.model.set_value(cdt, cdn, "price_impact_percent", 0);

		frappe.call({
			method: "buyback.api.get_question_options",
			args: { question_name: row.question },
			callback(r) {
				if (!r.message || !r.message.length) return;

				// Store options map on the row for answer_value change handler
				row._options_map = {};
				row._impact_map = {};
				r.message.forEach(o => {
					row._options_map[o.option_value] = o.option_label || o.option_value;
					row._impact_map[o.option_value] = o.price_impact_percent || 0;
				});

				const opts = r.message.map(o => o.option_value);
				frm.fields_dict.responses.grid.update_docfield_property(
					"answer_value", "options", ["" , ...opts].join("\n")
				);
				frm.fields_dict.responses.grid.refresh();
			},
		});
	},

	answer_value(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.answer_value) {
			if (row._options_map) {
				frappe.model.set_value(cdt, cdn, "answer_label",
					row._options_map[row.answer_value] || row.answer_value
				);
			}
			if (row._impact_map) {
				frappe.model.set_value(cdt, cdn, "price_impact_percent",
					row._impact_map[row.answer_value] || 0
				).then(() => buyback_recalculate_estimate(frm));
			} else if (row.question) {
				// Fallback: fetch from server
				frappe.call({
					method: "buyback.api.get_question_options",
					args: { question_name: row.question },
					callback(r) {
						if (!r.message) return;
						const opt = r.message.find(o => o.option_value === row.answer_value);
						if (opt) {
							frappe.model.set_value(cdt, cdn, "price_impact_percent",
								opt.price_impact_percent || 0
							).then(() => buyback_recalculate_estimate(frm));
						}
					},
				});
			}
		}
	},
});

// ── Automated Diagnostic Tests: dynamic result select + live depreciation ──
frappe.ui.form.on("Buyback Assessment Diagnostic", {
	test(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.test) return;

		frappe.call({
			method: "buyback.api.get_question_options",
			args: { question_name: row.test },
			callback(r) {
				if (!r.message || !r.message.length) return;

				// Store impact map on row for result change handler
				row._impact_map = {};
				r.message.forEach(o => {
					row._impact_map[o.option_value] = Math.abs(o.price_impact_percent || 0);
				});

				const opts = r.message.map(o => o.option_value);
				frm.fields_dict.diagnostic_tests.grid.update_docfield_property(
					"result", "options", "\n" + opts.join("\n")
				);
				frm.fields_dict.diagnostic_tests.grid.refresh();
			},
		});
	},

	result(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.result || !row.test) return;

		// Try local cache first (set by buyback_load_diagnostic_tests or test handler)
		if (row._impact_map && row._impact_map.hasOwnProperty(row.result)) {
			frappe.model.set_value(cdt, cdn, "depreciation_percent",
				row._impact_map[row.result]
			).then(() => buyback_recalculate_estimate(frm));
			return;
		}

		// Fallback: fetch from server
		frappe.call({
			method: "buyback.api.get_question_options",
			args: { question_name: row.test },
			callback(r) {
				if (!r.message) return;
				const opt = r.message.find(o => o.option_value === row.result);
				if (opt) {
					frappe.model.set_value(cdt, cdn, "depreciation_percent",
						Math.abs(opt.price_impact_percent || 0)
					).then(() => buyback_recalculate_estimate(frm));
				}
			},
		});
	},
});

// ── Live Estimate Recalculation ─────────────────────────────────
let _recalc_timer = null;
function buyback_recalculate_estimate(frm) {
	if (!frm.doc.item) return;

	// Debounce: wait 500ms after last change before calling server
	clearTimeout(_recalc_timer);
	_recalc_timer = setTimeout(() => {
		const diag = (frm.doc.diagnostic_tests || []).map(d => ({
			test: d.test,
			test_code: d.test_code,
			result: d.result,
			depreciation_percent: d.depreciation_percent,
		}));
		const resp = (frm.doc.responses || []).map(r => ({
			question: r.question,
			question_code: r.question_code,
			answer_value: r.answer_value,
			answer_label: r.answer_label,
			price_impact_percent: r.price_impact_percent,
		}));

		frappe.call({
			method: "buyback.api.calculate_live_estimate",
			args: {
				item_code: frm.doc.item,
				warranty_status: frm.doc.warranty_status || "",
				device_age_months: frm.doc.device_age_months || "",
				diagnostic_tests: JSON.stringify(diag),
				responses: JSON.stringify(resp),
				brand: frm.doc.brand || "",
				item_group: frm.doc.item_group || "",
			},
			callback(r) {
				if (!r.message) return;
				const est = r.message;

				// Update grade
				if (est.grade_id) {
					frm.set_value("estimated_grade", est.grade_id);
				}

				// Update price
				frm.set_value("estimated_price", est.estimated_price || 0);

				// Flash indicator
				if (est.estimated_price) {
					frappe.show_alert({
						message: __("Grade {0} — Estimated: ₹{1} (Base: ₹{2}, Deductions: ₹{3})",
							[est.grade, fmt_money(est.estimated_price), fmt_money(est.base_price), fmt_money(est.total_deductions)]),
						indicator: "green",
					}, 5);
				}
			},
		});
	}, 500);
}

// ── Reference Price Cards ───────────────────────────────────────
function buyback_render_price_cards(frm) {
	const wrapper = frm.fields_dict.price_cards_html;
	if (!wrapper) return;

	if (!frm.doc.item) {
		wrapper.$wrapper.html("");
		return;
	}

	frappe.call({
		method: "buyback.api.get_reference_prices",
		args: { item_code: frm.doc.item },
		callback(r) {
			if (!r.message) return;

			const mp = format_currency(r.message.market_price || 0, "INR");
			const vp = format_currency(r.message.vendor_price || 0, "INR");

			wrapper.$wrapper.html(`
				<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px;">
					<div style="flex:1;min-width:200px;background:var(--bg-blue);border:1px solid var(--blue-200);border-radius:var(--border-radius-lg);padding:16px 20px;">
						<div style="font-size:var(--text-xs);color:var(--blue-600);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Market Price</div>
						<div style="font-size:var(--text-2xl);font-weight:700;color:var(--blue-700);">${mp}</div>
					</div>
					<div style="flex:1;min-width:200px;background:var(--bg-green);border:1px solid var(--green-200);border-radius:var(--border-radius-lg);padding:16px 20px;">
						<div style="font-size:var(--text-xs);color:var(--green-600);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Vendor Price</div>
						<div style="font-size:var(--text-2xl);font-weight:700;color:var(--green-700);">${vp}</div>
					</div>
				</div>
			`);
		},
	});
}

// ── Auto-load Diagnostic Tests when Item is selected ────────────
function buyback_load_diagnostic_tests(frm) {
	if (!frm.doc.item) return;

	// Don't overwrite if tests already exist (e.g. editing a saved doc)
	if (frm.doc.diagnostic_tests && frm.doc.diagnostic_tests.length) return;

	frappe.call({
		method: "buyback.api.get_diagnostic_tests_for_item",
		args: { item_code: frm.doc.item },
		callback(r) {
			if (!r.message || !r.message.length) return;

			// Clear existing empty rows
			frm.clear_table("diagnostic_tests");

			r.message.forEach(test => {
				const row = frm.add_child("diagnostic_tests");
				row.test = test.name;
				row.test_code = test.test_code;
				row.test_name = test.test_name;

				// Pre-build impact map so result change sets depreciation live
				row._impact_map = {};
				if (test.options && test.options.length) {
					test.options.forEach(o => {
						row._impact_map[o.value] = Math.abs(o.impact || 0);
					});
				}
			});

			frm.refresh_field("diagnostic_tests");

			// Update result Select options for all rows
			if (r.message.length && r.message[0].options && r.message[0].options.length) {
				const opts = r.message[0].options.map(o => o.value);
				frm.fields_dict.diagnostic_tests.grid.update_docfield_property(
					"result", "options", "\n" + opts.join("\n")
				);
			}

			frappe.show_alert({
				message: __("{0} diagnostic tests loaded", [r.message.length]),
				indicator: "blue",
			});
		},
	});
}

// ── Auto-load Customer Questions when Item is selected ──────────
function buyback_load_customer_questions(frm) {
	if (!frm.doc.item) return;

	// Don't overwrite if questions already exist (e.g. editing a saved doc)
	if (frm.doc.responses && frm.doc.responses.length) return;

	frappe.call({
		method: "buyback.api.get_customer_questions_for_item",
		args: { item_code: frm.doc.item },
		callback(r) {
			if (!r.message || !r.message.length) return;

			// Clear existing empty rows
			frm.clear_table("responses");

			r.message.forEach(q => {
				const row = frm.add_child("responses");
				row.question = q.name;
				row.question_code = q.question_code;
				row.question_text = q.question_text;

				// Pre-build maps for live answer handling
				row._options_map = {};
				row._impact_map = {};
				row._answer_options = [];
				if (q.options && q.options.length) {
					q.options.forEach(o => {
						row._options_map[o.value] = o.label || o.value;
						row._impact_map[o.value] = o.impact || 0;
						row._answer_options.push(o.value);
					});
				}
			});

			frm.refresh_field("responses");

			// Each question may have different answer options (yes/no, scales, etc.)
			// Update the Select options dynamically when a row is clicked
			frm.fields_dict.responses.grid.wrapper.off("click.cq_opts");
			frm.fields_dict.responses.grid.wrapper.on("click.cq_opts", "[data-idx]", function () {
				const idx = $(this).attr("data-idx") || $(this).closest("[data-idx]").attr("data-idx");
				if (!idx) return;
				const row = frm.doc.responses[parseInt(idx) - 1];
				if (row && row._answer_options && row._answer_options.length) {
					frm.fields_dict.responses.grid.update_docfield_property(
						"answer_value", "options", ["", ...row._answer_options].join("\n")
					);
				}
			});

			frappe.show_alert({
				message: __("{0} customer questions loaded", [r.message.length]),
				indicator: "blue",
			});
		},
	});
}
