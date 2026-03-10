# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
# R13 — Settlement Register
# Full payout + exchange register for finance.
# Includes payment child-table rows and exchange-only orders.

import frappe
from frappe import _

from buyback.buyback.report.report_utils import date_condition, standard_conditions, in_condition
from buyback.buyback.constants import PAID_STATUSES


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{"fieldname": "settlement_date", "label": _("Settlement Date"), "fieldtype": "Date", "width": 120},
		{"fieldname": "order_name", "label": _("Order"), "fieldtype": "Link", "options": "Buyback Order", "width": 180},
		{"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "Warehouse", "width": 160},
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link", "options": "Customer", "width": 160},
		{"fieldname": "item", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 180},
		{"fieldname": "settlement_type", "label": _("Settlement Type"), "fieldtype": "Data", "width": 120},
		{"fieldname": "final_price", "label": _("Final Price"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "payment_method", "label": _("Payment Method"), "fieldtype": "Data", "width": 130},
		{"fieldname": "payment_amount", "label": _("Payment Amount"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "exchange_discount", "label": _("Exchange Discount"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "balance_to_pay", "label": _("Balance to Pay"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "transaction_reference", "label": _("Txn Ref"), "fieldtype": "Data", "width": 160},
	]


def get_data(filters):
	dc = date_condition("o.creation", filters)
	sc = standard_conditions(filters, alias="o.")
	paid_in = in_condition("o.status", PAID_STATUSES)

	# ── Buyback orders with payment rows ──
	payment_rows = frappe.db.sql("""
		SELECT
			p.payment_date AS settlement_date,
			o.name AS order_name,
			o.store,
			o.customer,
			o.item,
			o.settlement_type,
			o.final_price,
			p.payment_method,
			p.amount AS payment_amount,
			COALESCE(o.exchange_discount, 0) AS exchange_discount,
			COALESCE(o.balance_to_pay, 0) AS balance_to_pay,
			p.transaction_reference
		FROM `tabBuyback Order` o
		INNER JOIN `tabBuyback Order Payment` p ON p.parent = o.name
		WHERE {dc} {sc}
			AND {paid_in}
		ORDER BY p.payment_date DESC, o.name
	""".format(dc=dc, sc=sc, paid_in=paid_in), as_dict=True)

	# ── Exchange-only orders (no payment rows) ──
	exchange_rows = frappe.db.sql("""
		SELECT
			COALESCE(DATE(o.customer_approved_at), DATE(o.creation)) AS settlement_date,
			o.name AS order_name,
			o.store,
			o.customer,
			o.item,
			o.settlement_type,
			o.final_price,
			'' AS payment_method,
			0 AS payment_amount,
			COALESCE(o.exchange_discount, 0) AS exchange_discount,
			COALESCE(o.balance_to_pay, 0) AS balance_to_pay,
			'' AS transaction_reference
		FROM `tabBuyback Order` o
		WHERE {dc} {sc}
			AND {paid_in}
			AND o.settlement_type = 'Exchange'
			AND NOT EXISTS (
				SELECT 1 FROM `tabBuyback Order Payment` p WHERE p.parent = o.name
			)
		ORDER BY settlement_date DESC
	""".format(dc=dc, sc=sc, paid_in=paid_in), as_dict=True)

	data = payment_rows + exchange_rows
	data.sort(key=lambda r: r.get("settlement_date") or "", reverse=True)
	return data
