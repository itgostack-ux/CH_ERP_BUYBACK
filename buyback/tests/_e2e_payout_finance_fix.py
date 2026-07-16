# Copyright (c) 2026, GoStack and contributors
"""E2E regression — buyback payout finance closure on multi-company sites.

Reported 2026-07-14 (teammate's box, "Finance Closure Required" dialog):
  1. "Journal Entry ...: Account Cost of Goods Sold - G does not belong to
     Company BestBuy Mobiles Pvt Ltd" — Buyback Settings is a Single, so
     buyback_expense_account pointed at the OTHER company's ledger and the
     payout JE failed for every BestBuy order.
  2. 'Purpose cannot be "Buyback Payout"' — ch_payments sets that purpose
     on the Bank Payment Request, but the Select options never included it.
  3. "Cannot close ... missing: Journal Entry" — consequence of (1)/(2).

Fixes under test:
  * BuybackOrder._resolve_expense_account_for_company maps the configured
    ledger to the order's company by account_name (both cash JE and the
    bank accrual JE), skipping gracefully when no such ledger exists.
  * "Buyback Payout" added to Bank Payment Request.purpose options.

Run inside `bench --site erpnext.local console`:

    from buyback.tests._e2e_payout_finance_fix import run
    run()

Single transaction, rolled back — nothing committed.
"""

import frappe
from frappe.utils import flt

from buyback.tests._e2e_buyback_exchange_flow import _check_factory, _make_order


def _set_expense_account(account):
	frappe.db.set_single_value("Buyback Settings", "buyback_expense_account", account)
	frappe.clear_document_cache("Buyback Settings", "Buyback Settings")


def run():
	passed, failed, check = _check_factory()

	frappe.db.savepoint("e2e_payout_fin")
	original_acct = frappe.db.get_single_value("Buyback Settings", "buyback_expense_account")
	try:
		wrong_acct = "Cost of Goods Sold - GF"  # GOFIX ledger — replicates teammate's config
		if not frappe.db.exists("Account", wrong_acct):
			raise RuntimeError("Expected GOFIX COGS ledger not found")

		# ── Cash payout with wrong-company ledger configured ─────────────
		_set_expense_account(wrong_acct)
		order = _make_order(check, "FIN1")
		order.db_set("final_price", flt(order.final_price) or 5000, update_modified=False)
		order.customer_payout_mode = "Cash"
		order._create_journal_entry()
		check("cash JE created despite cross-company settings account",
			bool(order.journal_entry), order.journal_entry)
		if order.journal_entry:
			je = frappe.get_doc("Journal Entry", order.journal_entry)
			accounts = [r.account for r in je.accounts]
			check("cash JE: expense mapped to BestBuy ledger (- BM)",
				"Cost of Goods Sold - BM" in accounts, accounts)
			check("cash JE: submitted in order company",
				je.docstatus == 1 and je.company == order.company,
				f"{je.company}/{je.docstatus}")

		# ── Bank payout accrual JE with same wrong config ─────────────────
		order2 = _make_order(check, "FIN2")
		order2.db_set("final_price", flt(order2.final_price) or 5000, update_modified=False)
		order2.customer_payout_mode = "Bank Transfer"
		order2.customer_bank_account_number = "1234567890123"
		order2.customer_bank_ifsc = "HDFC0000123"
		order2._post_bank_payout_accrual_je()
		check("bank accrual JE created", bool(order2.journal_entry), order2.journal_entry)
		if order2.journal_entry:
			je2 = frappe.get_doc("Journal Entry", order2.journal_entry)
			accounts = [r.account for r in je2.accounts]
			check("accrual JE: expense mapped to BestBuy ledger",
				"Cost of Goods Sold - BM" in accounts, accounts)
			party_rows = [r for r in je2.accounts if r.party_type == "Customer"]
			check("accrual JE: customer sub-ledger credited",
				bool(party_rows) and flt(party_rows[0].credit_in_account_currency) > 0)

		# ── Correctly-scoped config passes through untouched ──────────────
		_set_expense_account("Cost of Goods Sold - BM")
		order3 = _make_order(check, "FIN3")
		order3.db_set("final_price", 4000, update_modified=False)
		order3.customer_payout_mode = "Cash"
		order3._create_journal_entry()
		je3 = frappe.get_doc("Journal Entry", order3.journal_entry)
		check("same-company config used as-is",
			"Cost of Goods Sold - BM" in [r.account for r in je3.accounts])

		# ── No matching ledger in order company → skip, don't crash ───────
		lonely = frappe.db.sql("""
			SELECT a.name FROM `tabAccount` a
			WHERE a.company = '_Test Company' AND a.is_group = 0
			  AND NOT EXISTS (
				SELECT 1 FROM `tabAccount` b
				WHERE b.company = %s AND b.account_name = a.account_name)
			LIMIT 1
		""", order.company)
		if lonely:
			_set_expense_account(lonely[0][0])
			order4 = _make_order(check, "FIN4")
			order4.db_set("final_price", 3000, update_modified=False)
			order4.customer_payout_mode = "Cash"
			order4._create_journal_entry()
			check("unmappable ledger: JE skipped gracefully (no crash)",
				not order4.journal_entry, order4.journal_entry)

		# ── BPR purpose Select accepts "Buyback Payout" ────────────────────
		bpr = frappe.new_doc("Bank Payment Request")
		bpr.purpose = "Buyback Payout"
		try:
			bpr._validate_selects()
			check("BPR purpose 'Buyback Payout' accepted", True)
		except Exception as exc:
			check("BPR purpose 'Buyback Payout' accepted", False, str(exc)[:120])
		bpr.purpose = "Totally Invalid Purpose"
		try:
			bpr._validate_selects()
			check("BPR invalid purpose still rejected", False, "no throw")
		except Exception:
			check("BPR invalid purpose still rejected", True)

	finally:
		# Nothing in this run commits, so the savepoint rollback also restores
		# the Buyback Settings account we changed in-transaction.
		frappe.db.rollback(save_point="e2e_payout_fin")
		frappe.clear_document_cache("Buyback Settings", "Buyback Settings")

	print(f"\n{len(passed)} passed, {len(failed)} failed")
	if failed:
		print("FAILED:", failed)
	return not failed
