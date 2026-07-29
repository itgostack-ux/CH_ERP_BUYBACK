frappe.ui.form.on("Buyback Assessment", {
	refresh(frm) {
		// Clear custom buttons first to prevent duplicates on repeated refreshes
		frm.clear_custom_buttons();

		// Detect POS origin — either from route_options or stored flag
		const from_pos = (frappe.route_options && frappe.route_options._from_pos)
			|| frm.doc.__from_pos;
		if (from_pos) {
			frm.doc.__from_pos = true; // persist across refresh
		}

		// Auto-fill store from POS session and make it read-only
		if (frm.is_new() && from_pos) {
			const pos_store = frappe.route_options && frappe.route_options.store;
			if (pos_store && !frm.doc.store) {
				frm.set_value("store", pos_store);
			}
		}
		// Lock store field if opened from POS (or if already saved with a store)
		if (from_pos || (!frm.is_new() && frm.doc.store)) {
			frm.set_df_property("store", "read_only", 1);
		}

		// ── Hide standard Submit / Amend buttons for submitted docs ──
		// We use our own custom workflow buttons instead
		if (frm.doc.docstatus === 1) {
			frm.page.clear_primary_action();
			frm.page.clear_secondary_action();
			// Hide the standard "Amend" button that Frappe auto-adds
			setTimeout(() => {
				frm.page.btn_secondary && frm.page.btn_secondary.addClass("hide");
				frm.page.actions_btn_group && frm.page.actions_btn_group.addClass("hide");
			}, 100);
		}

		// ── "Back to POS" button (only when opened from POS) ──
		if (from_pos && !frm.is_new()) {
			frm.add_custom_button(__("Back to POS"), () => {
				window.history.back();
			}).addClass("btn-default");
		}

		// "New Customer" quick-entry button (always visible in Draft / unsaved)
		if (!frm.doc.docstatus && (!frm.doc.status || frm.doc.status === "Draft")) {
			frm.add_custom_button(__("New Customer"), () => {
				buyback_open_new_customer_dialog(frm);
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
					`<a href="/desk/buyback-inspection/${frm.doc.buyback_inspection}">${frm.doc.buyback_inspection}</a>`,
				]),
				"blue",
				true
			);
		}

		// Grid row controls:
		//   • "Add row" always disabled — rows are auto-loaded from item diagnostics/questions.
		//   • Row-select checkbox disabled (cannot_delete_rows=true) so the
		//     "Delete row / Edit row / Duplicate row" toolbar never appears.
		//     Users answer inline (Result / Answer dropdowns) but cannot
		//     remove, duplicate, or bulk-edit rows.
		//   • Result/Answer are rendered as inline radio groups below. Keep the
		//     underlying Select fields read-only even in Draft so a grid click can
		//     never replace a radio group with Frappe's native dropdown editor.
		frm.fields_dict.diagnostic_tests.grid.cannot_add_rows = true;
		frm.fields_dict.diagnostic_tests.grid.cannot_delete_rows = true;
		frm.fields_dict.diagnostic_tests.grid.update_docfield_property(
			"depreciation_percent", "hidden", 1
		);
		frm.fields_dict.diagnostic_tests.grid.update_docfield_property(
			"result", "read_only", 1
		);
		frm.fields_dict.diagnostic_tests.grid.refresh();

		frm.fields_dict.responses.grid.cannot_add_rows = true;
		frm.fields_dict.responses.grid.cannot_delete_rows = true;
		frm.fields_dict.responses.grid.update_docfield_property(
			"answer_value", "read_only", 1
		);
		frm.fields_dict.responses.grid.refresh();

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
		if (frm.doc.item && frm.doc.status === "Draft" && !frm.doc.is_phone_dead) {
			buyback_load_diagnostic_tests(frm);
			buyback_load_customer_questions(frm);
		}

		_buyback_set_phone_dead_state(frm, false);
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
	
	device_age_months(frm) {
    const age = frm.doc.device_age_months;
    
    if (!age || age.trim() === "") {
        frm.set_value("warranty_status", null);
    } else if (age === "12+ Months") {
        frm.set_value("warranty_status", "Out of Warranty");
    } else {
        frm.set_value("warranty_status", "In Warranty");
    }
    buyback_render_price_cards(frm);
    buyback_recalculate_estimate(frm);
	},

	warranty_status(frm) { buyback_render_price_cards(frm); buyback_recalculate_estimate(frm); },

	is_phone_dead(frm) {
		_buyback_handle_phone_dead_toggle(frm);
	},

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
		setTimeout(() => _buyback_render_response_radios(frm), 0);
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

				_buyback_set_diagnostic_option_maps(row, r.message.map(option => ({
					value: option.option_value,
					label: option.option_label || option.option_value,
					impact: option.price_impact_percent || 0,
				})));

				const opts = r.message.map(o => o.option_value);
				frm.fields_dict.diagnostic_tests.grid.update_docfield_property(
					"result", "options", "\n" + opts.join("\n")
				);
				frm.fields_dict.diagnostic_tests.grid.refresh();
				_buyback_render_diagnostic_radios(frm);
			},
		});
	},

	result(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.result || !row.test) return;

		//User selected a LABEL (Yes/No)  convert to VALUE (Pass/Fail)
		let actual_value = row.result;
		if (row._label_to_value && row._label_to_value.hasOwnProperty(row.result)) {
			actual_value = row._label_to_value[row.result];
			// Silently update the row to store the canonical value
			if (actual_value !== row.result) {
				frappe.model.set_value(cdt, cdn, "result", actual_value);
				return; // set_value will re-trigger this handler with correct value
			}
		}

		// Now lookup impact using the value
		if (row._impact_map && row._impact_map.hasOwnProperty(actual_value)) {
			frappe.model.set_value(cdt, cdn, "depreciation_percent",
				row._impact_map[actual_value]
			).then(() => buyback_recalculate_estimate(frm));
			return;
		}

		// Fallback: fetch from server
		frappe.call({
			method: "buyback.api.get_question_options",
			args: { question_name: row.test },
			callback(r) {
				if (!r.message) return;
				const opt = r.message.find(o => o.option_value === actual_value);
				if (opt) {
					frappe.model.set_value(cdt, cdn, "depreciation_percent",
						Math.abs(opt.price_impact_percent || 0)
					).then(() => buyback_recalculate_estimate(frm));
				}
			},
		});
		setTimeout(() => _buyback_render_diagnostic_radios(frm), 0);
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
				is_phone_dead: frm.doc.is_phone_dead ? 1 : 0, 
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

				// Intentionally avoid toast notifications; only update fields on the form.
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
	if (!frm.doc.item || frm.doc.is_phone_dead) return;

	frappe.call({
		method: "buyback.api.get_diagnostic_tests_for_item",
		args: { item_code: frm.doc.item },
		callback(r) {
			if (frm.doc.is_phone_dead) return;
			if (!r.message || !r.message.length) return;
			const existing_rows = frm.doc.diagnostic_tests || [];
			const tests_by_name = Object.fromEntries(r.message.map(test => [test.name, test]));

			// API-created and previously saved assessments already contain rows.
			// Rehydrate option metadata so their stored value/label is preselected.
			if (existing_rows.length) {
				existing_rows.forEach(row => {
					const test = tests_by_name[row.test];
					if (test) _buyback_set_diagnostic_option_maps(row, test.options);
				});
				_buyback_render_diagnostic_radios(frm);
				return;
			}

			// Clear existing empty rows
			frm.clear_table("diagnostic_tests");

			r.message.forEach(test => {
				const row = frm.add_child("diagnostic_tests");
				row.test = test.name;
				row.test_code = test.test_code;
				row.test_name = test.test_name;

				_buyback_set_diagnostic_option_maps(row, test.options);
            });

			frm.refresh_field("diagnostic_tests");

			// Update result Select options for all rows
            if (r.message.length && r.message[0].options && r.message[0].options.length) {
                const opts = r.message[0].options.map(o => o.label || o.value);
				frm.fields_dict.diagnostic_tests.grid.update_docfield_property(
					"result", "options", "\n" + opts.join("\n")
				);
			}
			_buyback_render_diagnostic_radios(frm);

			frappe.show_alert({
				message: __("{0} diagnostic tests loaded", [r.message.length]),
				indicator: "blue",
			});
		},
	});
}

function _buyback_set_diagnostic_option_maps(row, options) {
	row._impact_map = {};
	row._label_to_value = {};
	row._diagnostic_radio_options = options || [];
	row._diagnostic_radio_options.forEach(option => {
		row._impact_map[option.value] = Math.abs(option.impact || 0);
		row._label_to_value[option.label || option.value] = option.value;
	});
}

function _buyback_option_is_selected(stored_value, option) {
	const stored = String(stored_value || "").trim().toLowerCase();
	return Boolean(stored) && [option.value, option.label]
		.some(value => String(value || "").trim().toLowerCase() === stored);
}

function _buyback_render_diagnostic_radios(frm) {
	const grid = frm.fields_dict.diagnostic_tests && frm.fields_dict.diagnostic_tests.grid;
	if (!grid || frm.doc.is_phone_dead) return;

	grid.wrapper.off("change.buyback_diagnostic_radio");
	grid.wrapper.on("change.buyback_diagnostic_radio", ".buyback-diagnostic-radio", function (event) {
		event.stopPropagation();
		const row_name = $(this).closest(".grid-row").attr("data-name");
		const row = (frm.doc.diagnostic_tests || []).find(item => item.name === row_name);
		if (!row) return;
		frappe.model.set_value(row.doctype, row.name, "result", $(this).val())
			.then(() => setTimeout(() => _buyback_render_diagnostic_radios(frm), 0));
	});

	(grid.grid_rows || []).forEach(grid_row => {
		const row = grid_row.doc;
		const options = row && row._diagnostic_radio_options;
		if (!row || !options || !options.length) return;

		const $column = grid_row.wrapper.find('[data-fieldname="result"]').first();
		if (!$column.length) return;
		$column.find(".field-area, .static-area").hide();
		$column.find(".buyback-diagnostic-radios").remove();

		const disabled = frm.doc.docstatus ? "disabled" : "";
		const choices = options.map(option => {
			const value = frappe.utils.escape_html(option.value || "");
			const label = frappe.utils.escape_html(option.label || option.value || "");
			const checked = _buyback_option_is_selected(row.result, option) ? "checked" : "";
			return `<label style="display:inline-flex;align-items:center;gap:5px;margin:2px 12px 2px 0;white-space:nowrap;cursor:pointer">
				<input class="buyback-diagnostic-radio" type="radio" name="buyback-diagnostic-${frappe.utils.escape_html(row.name)}" value="${value}" ${checked} ${disabled} style="margin:0;accent-color:var(--primary)" />
				<span>${label}</span>
			</label>`;
		}).join("");
		$column.append(`<div class="buyback-diagnostic-radios" style="display:flex;flex-wrap:wrap;align-items:center;min-height:38px;padding:4px 8px">${choices}</div>`);
		// This column is rendered as a radio group, not as Frappe's Select
		// editor. Stop pointer events at the cell before they reach the grid-row
		// click handler, otherwise clicking Yes/No first opens the dropdown on
		// top of the radios. Do not preventDefault: the browser must still check
		// the selected radio and emit change for frappe.model.set_value above.
		$column.off("mousedown.buyback_radio click.buyback_radio");
		$column.on("mousedown.buyback_radio click.buyback_radio", function (event) {
			event.stopPropagation();
		});
	});
}

// ── Auto-load Customer Questions when Item is selected ──────────
function buyback_load_customer_questions(frm) {
	if (!frm.doc.item || frm.doc.is_phone_dead) return;

	frappe.call({
		method: "buyback.api.get_customer_questions_for_item",
		args: { item_code: frm.doc.item },
		callback(r) {
			if (frm.doc.is_phone_dead) return;
			if (!r.message || !r.message.length) return;
			const existing_rows = frm.doc.responses || [];
			const questions_by_name = Object.fromEntries(
				r.message.map(question => [question.name, question])
			);

			// Saved assessments already have rows. Rehydrate their configured
			// options so the inline radios can be rendered after every refresh.
			if (existing_rows.length) {
				existing_rows.forEach(row => {
					const question = questions_by_name[row.question];
					if (question) _buyback_set_response_option_maps(row, question.options);
				});
				_buyback_render_response_radios(frm);
				return;
			}

			// Clear existing empty rows
			frm.clear_table("responses");

			r.message.forEach(q => {
				const row = frm.add_child("responses");
				row.question = q.name;
				row.question_code = q.question_code;
				row.question_text = q.question_text;

				_buyback_set_response_option_maps(row, q.options);
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
			_buyback_render_response_radios(frm);

			frappe.show_alert({
				message: __("{0} customer questions loaded", [r.message.length]),
				indicator: "blue",
			});
		},
	});
}

function _buyback_set_response_option_maps(row, options) {
	row._options_map = {};
	row._impact_map = {};
	row._answer_options = [];
	row._answer_radio_options = options || [];
	row._answer_radio_options.forEach(option => {
		row._options_map[option.value] = option.label || option.value;
		row._impact_map[option.value] = option.impact || 0;
		row._answer_options.push(option.value);
	});
}

function _buyback_render_response_radios(frm) {
	const grid = frm.fields_dict.responses && frm.fields_dict.responses.grid;
	if (!grid || frm.doc.is_phone_dead) return;

	grid.wrapper.off("change.buyback_answer_radio");
	grid.wrapper.on("change.buyback_answer_radio", ".buyback-answer-radio", function (event) {
		event.stopPropagation();
		const row_name = $(this).closest(".grid-row").attr("data-name");
		const row = (frm.doc.responses || []).find(item => item.name === row_name);
		if (!row) return;
		frappe.model.set_value(row.doctype, row.name, "answer_value", $(this).val())
			.then(() => setTimeout(() => _buyback_render_response_radios(frm), 0));
	});

	(grid.grid_rows || []).forEach(grid_row => {
		const row = grid_row.doc;
		const options = row && row._answer_radio_options;
		if (!row || !options || !options.length) return;

		const $column = grid_row.wrapper.find('[data-fieldname="answer_value"]').first();
		if (!$column.length) return;
		$column.find(".field-area, .static-area").hide();
		$column.find(".buyback-answer-radios").remove();

		const disabled = frm.doc.docstatus ? "disabled" : "";
		const choices = options.map(option => {
			const value = frappe.utils.escape_html(option.value || "");
			const label = frappe.utils.escape_html(option.label || option.value || "");
			const checked = _buyback_option_is_selected(row.answer_value, option) ? "checked" : "";
			return `<label style="display:inline-flex;align-items:center;gap:5px;margin:2px 12px 2px 0;white-space:nowrap;cursor:pointer">
				<input class="buyback-answer-radio" type="radio" name="buyback-answer-${frappe.utils.escape_html(row.name)}" value="${value}" ${checked} ${disabled} style="margin:0;accent-color:var(--primary)" />
				<span>${label}</span>
			</label>`;
		}).join("");
		$column.append(`<div class="buyback-answer-radios" style="display:flex;flex-wrap:wrap;align-items:center;min-height:38px;padding:4px 8px">${choices}</div>`);
		$column.off("mousedown.buyback_radio click.buyback_radio");
		$column.on("mousedown.buyback_radio click.buyback_radio", function (event) {
			event.stopPropagation();
		});
	});
}

function buyback_normalize_phone(value) {
	const digits = String(value || "").replace(/\D/g, "");
	if (digits.length >= 10) return digits.slice(-10);
	return digits;
}

function buyback_is_valid_indian_phone(value) {
	const normalized = buyback_normalize_phone(value);
	return /^[6-9]\d{9}$/.test(normalized);
}

function buyback_status_html(message, color = "#64748b") {
	return `<div style="font-size:12px;color:${color};padding-top:4px">${frappe.utils.escape_html(message || "")}</div>`;
}


function buyback_open_new_customer_dialog(frm) {
	window.ch_open_new_customer_dialog({
		company: frappe.defaults.get_default("company"),
		on_success: (name) => {
			frm.set_value("customer", name);
		},
		on_use_existing: (customer) => {
			frm.set_value("customer", customer);
		},
	});
}

// ── Handle Phone Dead checkbox toggle ────────────────────────────
function _buyback_handle_phone_dead_toggle(frm) {
	_buyback_set_phone_dead_state(frm, true);
	buyback_recalculate_estimate(frm);
}

function _buyback_set_phone_dead_state(frm, reload_when_alive) {
	const is_dead = Boolean(frm.doc.is_phone_dead);
	frm.set_df_property("diagnostic_tests_section", "hidden", is_dead ? 1 : 0);
	frm.set_df_property("diagnostic_tests", "hidden", is_dead ? 1 : 0);
	frm.set_df_property("responses_section", "hidden", is_dead ? 1 : 0);
	frm.set_df_property("responses", "hidden", is_dead ? 1 : 0);

	if (is_dead) {
		frm.clear_table("diagnostic_tests");
		frm.clear_table("responses");
		frm.refresh_field("diagnostic_tests");
		frm.refresh_field("responses");
	} else if (reload_when_alive && frm.doc.item && frm.doc.status === "Draft") {
		buyback_load_diagnostic_tests(frm);
		buyback_load_customer_questions(frm);
	}
}
