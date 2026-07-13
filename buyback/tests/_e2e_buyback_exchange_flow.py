# Copyright (c) 2026, GoStack and contributors
"""E2E — buyback pricing/grading, customer-approval payout crash regression,
KYC, and buyback/exchange bin routing.

Run inside `bench --site erpnext.local console`:

    from buyback.tests._e2e_buyback_exchange_flow import run
    run()

Whole run is one transaction, rolled back at the end. frappe.db.commit is
no-opped for the duration (move_to_bin and friends commit internally).
"""

import frappe
from frappe.utils import flt


def _check_factory():
	passed, failed = [], []

	def check(label, ok, detail=""):
		(passed if ok else failed).append(label)
		print(f"{'PASS' if ok else 'FAIL'} — {label}{(' :: ' + str(detail)) if detail and not ok else ''}")

	return passed, failed, check


def _fake_imei(tag):
	import random
	return f"35{random.randint(10**12, 10**13 - 1)}"  # 15-digit numeric


# ─────────────────────────────────────────────────────────────────
# Section 1 — Pricing engine: base price, % deduction, grade
# ─────────────────────────────────────────────────────────────────

def _test_pricing(check):
	from buyback.api import get_estimate
	from buyback.buyback.pricing.engine import _round_price

	row = frappe.db.sql(
		"""
		SELECT pm.item_code, pm.a_grade_iw_0_3, pm.b_grade_iw_0_3, pm.c_grade_iw_0_3
		  FROM `tabBuyback Price Master` pm
		 WHERE pm.is_active = 1 AND IFNULL(pm.a_grade_iw_0_3, 0) > 0
		 LIMIT 1
		""",
		as_dict=True,
	)
	if not row:
		check("pricing: active Buyback Price Master with A-grade price exists", False, "none found")
		return
	pm = row[0]

	# NB: option percents are stored NEGATIVE on this site (-35..-5); the
	# engine applies abs(), so match on ABS here and pick the smallest.
	opt = frappe.db.sql(
		"""
		SELECT qb.question_code, qo.option_value, qo.price_impact_percent
		  FROM `tabBuyback Question Option` qo
		  JOIN `tabBuyback Question Bank` qb ON qb.name = qo.parent
		 WHERE qb.disabled = 0
		   AND ABS(IFNULL(qo.price_impact_percent, 0)) >= 1
		 ORDER BY ABS(qo.price_impact_percent)
		 LIMIT 1
		""",
		as_dict=True,
	)
	if not opt:
		check("pricing: question option with % impact exists", False, "none found")
		return
	q = opt[0]

	est = get_estimate(
		item_code=pm.item_code,
		grade="A",
		warranty_status="In Warranty",
		device_age_months=2,
		responses=frappe.as_json([
			{"question_code": q.question_code, "answer_value": q.option_value}
		]),
	)

	base = flt(est.get("base_price"))
	check(
		"pricing: base price = Grade A ready-reckoner (iw_0_3 bucket)",
		base == flt(pm.a_grade_iw_0_3),
		f"got {base}, master {pm.a_grade_iw_0_3}",
	)

	expected_ded = abs(base * flt(q.price_impact_percent) / 100)
	q_deds = [d for d in est.get("deductions", []) if d.get("type") == "question"]
	check(
		f"pricing: answer '{q.option_value}' deducts exactly {q.price_impact_percent}% of base",
		q_deds and abs(flt(q_deds[0]["amount"]) - expected_ded) < 0.01,
		f"got {q_deds}, expected {expected_ded}",
	)

	total_ded = flt(est.get("total_deductions"))
	if not est.get("is_scrap"):
		check(
			"pricing: estimated = round(base - total deductions)",
			flt(est.get("estimated_price")) == flt(_round_price(base - total_ded)),
			f"est {est.get('estimated_price')}, base {base}, ded {total_ded}",
		)
	else:
		check("pricing: scrap floor applied (grade E)", est.get("grade_letter") == "E", est)

	check(
		"pricing: grade letter returned and valid",
		est.get("grade_letter") in ("A", "B", "C", "D", "E", "F"),
		est.get("grade_letter"),
	)
	# Grade must be consistent with the price bands: if a deduction pulled the
	# price below the A-grade price, the mapped grade cannot still be A.
	if not est.get("is_scrap") and total_ded > 0 and flt(est.get("estimated_price")) < flt(pm.a_grade_iw_0_3):
		check(
			"pricing: grade downgraded when price drops below A-grade band",
			est.get("grade_letter") != "A" or flt(est.get("estimated_price")) >= flt(pm.b_grade_iw_0_3 or 0),
			est,
		)
	return est


def _test_pos_grading(check):
	"""POS condition-check grading: failed checks must deduct and downgrade."""
	clean = _make_assessment(check, "GRD-CLEAN")
	damaged = _make_assessment(
		check, "GRD-DMG",
		condition_checks={"screen": False, "body": True, "buttons": True,
			"charging": True, "camera": False, "speaker_mic": True},
	)
	check("POS grading: assessment created with grade + price",
		bool(clean.get("grade")) and flt(clean.get("estimated_price")) > 0, clean)
	check("POS grading: failed screen/body checks deduct from price",
		flt(damaged.get("estimated_price")) < flt(clean.get("estimated_price")),
		f"clean={clean.get('estimated_price')} damaged={damaged.get('estimated_price')}")
	check("POS grading: deduction lines recorded for failed checks",
		len(damaged.get("deductions") or []) > len(clean.get("deductions") or []),
		damaged.get("deductions"))
	doc_grade, doc_quote = frappe.db.get_value(
		"Buyback Assessment", clean["name"], ["estimated_grade", "quoted_price"]
	)
	check("POS grading: grade + quoted price persisted on assessment doc",
		bool(doc_grade) and flt(doc_quote) == flt(clean["estimated_price"]),
		{"grade": doc_grade, "quote": doc_quote})


# ─────────────────────────────────────────────────────────────────
# Section 2 — Approval-link payout crash regression + approve + KYC
# ─────────────────────────────────────────────────────────────────

def _price_master_item():
	item = frappe.db.get_value(
		"Buyback Price Master",
		{"is_active": 1, "a_grade_iw_0_3": (">", 0)},
		"item_code",
	)
	if not item:
		raise RuntimeError("No active Buyback Price Master with A-grade price")
	return item


def _make_assessment(check, tag, condition_checks=None):
	"""Create an assessment through the real POS grading API."""
	from ch_pos.api.pos_api import create_buyback_assessment_with_grading

	customer = frappe.db.get_value("Customer", {"disabled": 0}, "name")
	out = create_buyback_assessment_with_grading(
		mobile_no="9876500011",
		item_code=_price_master_item(),
		imei_serial=_fake_imei(tag),
		customer=customer,
		condition_checks=condition_checks
			or {"screen": True, "body": True, "buttons": True,
				"charging": True, "camera": True, "speaker_mic": True},
		kyc_id_type="Aadhar Card",
		kyc_id_number="234567890123",
		kyc_name="Nivetha",
	)
	return out


def _make_order(check, tag):
	from ch_pos.api.pos_api import pos_start_buyback_order

	assessment = _make_assessment(check, tag)
	profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name")
	out = pos_start_buyback_order(
		assessment_name=assessment["name"],
		pos_profile=profile,
		account_lock_cleared=1,
		account_lock_check_notes="e2e: customer removed FRP/iCloud lock in store",
	)
	order = frappe.get_doc("Buyback Order", out["order_name"])
	if not order.imei_serial:
		order.db_set("imei_serial", _fake_imei(tag), update_modified=False)
		order.reload()
	return order


def _test_approval_payout_kyc(check):
	from buyback.api import (
		save_customer_payout_preference,
		customer_approve_via_token,
		verify_kyc,
	)

	order = _make_order(check, "BB")
	if not order.approval_token:
		order.db_set("approval_token", frappe.generate_hash(length=32), update_modified=False)
		order.reload()

	# Force the EXACT crash precondition from the bug report: POS advanced
	# `status` via db_set while the workflow mirror stayed at Approved.
	order.db_set("status", "Approved", update_modified=True)
	if order.meta.has_field("workflow_state"):
		order.db_set("workflow_state", "Approved", update_modified=False)
	order.db_set("status", "Awaiting Customer Approval", update_modified=True)
	order.reload()
	stale = order.meta.has_field("workflow_state") and order.workflow_state == "Approved"
	check("setup: stale workflow mirror reproduced (status ahead of workflow_state)", stale,
		f"status={order.status} wf={order.get('workflow_state')}")

	# The reported crash: guest payout save (screenshot: Bank Transfer / Nivetha)
	try:
		res = save_customer_payout_preference(
			token=order.approval_token,
			payout_mode="Bank Transfer",
			bank_account_holder="Nivetha",
			bank_account_number="1234567890123",
			bank_ifsc="hdfc0001234",
			bank_name="HDFC Bank",
		)
		order.reload()
		check("REGRESSION: payout save via approval link no longer crashes", True)
		check("payout: bank details stored (IFSC upper-cased)",
			order.customer_bank_account_holder == "Nivetha"
			and order.customer_bank_ifsc == "HDFC0001234",
			{"holder": order.customer_bank_account_holder, "ifsc": order.customer_bank_ifsc})
	except Exception as e:
		check("REGRESSION: payout save via approval link no longer crashes", False, repr(e))
		return None

	# Customer approves via the same token
	customer_approve_via_token(order.approval_token, method="SMS Link")
	order.reload()
	check("customer approval: status → Customer Approved + flag set",
		order.status == "Customer Approved" and order.customer_approved == 1,
		order.status)
	check("customer approval: workflow mirror synced",
		not order.meta.has_field("workflow_state") or order.workflow_state == order.status,
		order.get("workflow_state"))

	# Payout details LOCK after approval (security: bank-account hijack)
	try:
		save_customer_payout_preference(
			token=order.approval_token, payout_mode="UPI", upi_id="evil@upi")
		check("security: payout locked after customer approval", False, "save was allowed!")
	except Exception:
		check("security: payout locked after customer approval", True)

	# KYC: capture + staff verification
	order.db_set({
		"customer_id_type": "Aadhar Card",
		"customer_id_number": "234567890123",
		"customer_photo": "/files/e2e_photo.jpg",
		"customer_id_front": "/files/e2e_id_front.jpg",
	}, update_modified=False)
	order.reload()
	verify_kyc(order.name)
	order.reload()
	check("kyc: verified with id proof + photo (verified_by stamped)",
		order.kyc_verified == 1 and bool(order.kyc_verified_by), order.kyc_verified)
	return order


# ─────────────────────────────────────────────────────────────────
# Section 3 — Buyback settle → device into Buyback bin, not sellable
# ─────────────────────────────────────────────────────────────────

def _bin_state(serial):
	sn = frappe.db.get_value("Serial No", serial, ["warehouse", "status"], as_dict=True) or frappe._dict()
	bin_type = frappe.db.get_value("CH Stock Bin", {"serial_no": serial}, "bin_type")
	return sn.get("warehouse"), bin_type, sn.get("status")


def _test_buyback_bin_routing(check, order):
	from buyback.utils import resolve_store_bin_warehouse
	from ch_pos.api.pos_api import pos_settle_buyback_cashback
	from ch_pos.api.search import get_available_serials

	from buyback.lifecycle_api import record_indemnity

	serial = order.imei_serial
	record_indemnity(
		order.name,
		signed_by_name="Nivetha",
		signature_type="E-Signature (Kiosk)",
		notes="e2e indemnity",
	)
	order.reload()
	check("buyback gate: indemnity/NOC recorded before payout",
		order.indemnity_signed == 1, order.indemnity_signed)

	pos_settle_buyback_cashback(order.name, "Cash")
	order.reload()
	check("buyback settle: order Paid/Closed", order.status in ("Paid", "Closed"), order.status)
	check("buyback settle: stock entry created", bool(order.stock_entry), order.stock_entry)

	buyback_wh = resolve_store_bin_warehouse(order.store, order.company, "Buyback")
	sellable_wh = resolve_store_bin_warehouse(order.store, order.company, "Sellable")
	wh, bin_type, sn_status = _bin_state(serial)

	check("buyback bins: device physically in store's BUYBACK bin warehouse",
		wh == buyback_wh, f"wh={wh}, expected={buyback_wh}")
	check("buyback bins: serial tagged bin_type=Buyback",
		bin_type == "Buyback", bin_type)

	if sellable_wh:
		sellable = {r["serial_no"] for r in get_available_serials(order.item, sellable_wh)}
		check("buyback bins: IMEI NOT offered in POS sellable picker",
			serial not in sellable, f"{serial} in {len(sellable)} sellable serials")
	else:
		check("buyback bins: sellable warehouse resolved", False, "no sellable bin for store")


# ─────────────────────────────────────────────────────────────────
# Section 4 — Exchange: reserved-in-sellable until invoice, then Buyback
# ─────────────────────────────────────────────────────────────────

def _test_exchange_bin_routing(check):
	from buyback.utils import resolve_store_bin_warehouse
	from ch_pos.api.search import get_available_serials

	order = _make_order(check, "EX")
	order.db_set("settlement_type", "Exchange", update_modified=False)
	order.reload()

	# Phase 1 — device received during exchange: physically in SELLABLE
	# warehouse, logically RESERVED (held for this customer).
	order._create_stock_entry()
	serial = order.imei_serial
	sellable_wh = resolve_store_bin_warehouse(order.store, order.company, "Sellable")
	buyback_wh = resolve_store_bin_warehouse(order.store, order.company, "Buyback")

	wh, bin_type, _ = _bin_state(serial)
	check("exchange phase 1: traded-in IMEI physically in SELLABLE warehouse",
		wh == sellable_wh, f"wh={wh}, expected={sellable_wh}")
	check("exchange phase 1: serial tagged RESERVED (held for customer)",
		bin_type == "Reserved", bin_type)
	sellable = {r["serial_no"] for r in get_available_serials(order.item, sellable_wh or "")}
	check("exchange phase 1: RESERVED IMEI not offered to other walk-ins",
		serial not in sellable, serial)

	# Phase 2 — exchange invoice completed → device retires to Buyback bin.
	# _move_old_device_to_buyback_bin is exactly what the Sales Invoice
	# on_submit hook (move_traded_device_to_buyback_on_invoice) calls.
	exo = frappe.get_doc({
		"doctype": "Buyback Exchange Order",
		"company": order.company,
		"store": order.store,
		"customer": order.customer,
		"mobile_no": order.mobile_no,
		"old_imei_serial": serial,
	})
	exo.name = "E2E-EXO-VIRTUAL"  # not inserted — drives the real move logic
	exo._move_old_device_to_buyback_bin()

	wh2, bin_type2, _ = _bin_state(serial)
	check("exchange phase 2: after invoice completion IMEI moved to BUYBACK bin",
		wh2 == buyback_wh, f"wh={wh2}, expected={buyback_wh}")
	check("exchange phase 2: serial re-tagged bin_type=Buyback",
		bin_type2 == "Buyback", bin_type2)
	sellable2 = {r["serial_no"] for r in get_available_serials(order.item, sellable_wh or "")}
	check("exchange phase 2: IMEI no longer in sellable picker",
		serial not in sellable2, serial)

	# Wiring: the SI on_submit hook must be registered
	import buyback.hooks as bh
	si_hooks = str(bh.doc_events.get("Sales Invoice", {}))
	check("exchange wiring: SI on_submit hook registered",
		"move_traded_device_to_buyback_on_invoice" in si_hooks, si_hooks[:120])


# ─────────────────────────────────────────────────────────────────

def run():
	passed, failed, check = _check_factory()

	real_commit = frappe.db.commit
	frappe.db.commit = lambda *a, **k: None  # move_to_bin & co. commit internally
	frappe.db.savepoint("e2e_buyback_flow")
	try:
		print("── Section 1: pricing / grading / % deduction ──")
		_test_pricing(check)
		_test_pos_grading(check)

		print("\n── Section 2: approval-link payout crash + customer approval + KYC ──")
		order = _test_approval_payout_kyc(check)

		if order:
			print("\n── Section 3: buyback → Buyback bin routing ──")
			try:
				_test_buyback_bin_routing(check, order)
			except Exception as e:
				check("buyback bin routing completed", False, repr(e))

		print("\n── Section 4: exchange → reserved-then-buyback routing ──")
		try:
			_test_exchange_bin_routing(check)
		except Exception as e:
			check("exchange bin routing completed", False, repr(e))

	finally:
		frappe.db.rollback(save_point="e2e_buyback_flow")
		frappe.db.commit = real_commit

	print(f"\n{len(passed)} passed, {len(failed)} failed")
	if failed:
		print("FAILED:", failed)
	return not failed
