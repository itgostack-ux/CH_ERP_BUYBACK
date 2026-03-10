# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
# R16 — Exchange Conversion
# After approval, how many became exchange vs buyback per store.
# Bar chart: buyback vs exchange by branch.

import frappe
from frappe import _

from buyback.buyback.report.report_utils import date_condition, standard_conditions, in_condition
from buyback.buyback.constants import PAID_STATUSES


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)
	return columns, data, None, chart


def get_columns():
	return [
		{"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "Warehouse", "width": 180},
		{"fieldname": "total_settled", "label": _("Total Settled"), "fieldtype": "Int", "width": 110},
		{"fieldname": "buyback_count", "label": _("Buyback Count"), "fieldtype": "Int", "width": 120},
		{"fieldname": "buyback_value", "label": _("Buyback Value"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "exchange_count", "label": _("Exchange Count"), "fieldtype": "Int", "width": 120},
		{"fieldname": "exchange_value", "label": _("Exchange Value"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "exchange_pct", "label": _("Exchange %"), "fieldtype": "Percent", "width": 110},
	]


def get_data(filters):
	dc = date_condition("o.creation", filters)
	sc = standard_conditions(filters, alias="o.")
	paid_in = in_condition("o.status", PAID_STATUSES)

	rows = frappe.db.sql("""
		SELECT
			o.store,
			COUNT(*) AS total_settled,
			SUM(CASE WHEN o.settlement_type = 'Buyback' THEN 1 ELSE 0 END) AS buyback_count,
			COALESCE(SUM(CASE WHEN o.settlement_type = 'Buyback'
				THEN COALESCE(o.final_price, 0) ELSE 0 END), 0) AS buyback_value,
			SUM(CASE WHEN o.settlement_type = 'Exchange' THEN 1 ELSE 0 END) AS exchange_count,
			COALESCE(SUM(CASE WHEN o.settlement_type = 'Exchange'
				THEN COALESCE(o.final_price, 0) ELSE 0 END), 0) AS exchange_value
		FROM `tabBuyback Order` o
		WHERE {dc} {sc}
			AND {paid_in}
		GROUP BY o.store
		ORDER BY total_settled DESC
	""".format(dc=dc, sc=sc, paid_in=paid_in), as_dict=True)

	for r in rows:
		r["exchange_pct"] = round(
			r["exchange_count"] / r["total_settled"] * 100, 1
		) if r["total_settled"] else 0

	return rows


def get_chart(data):
	if not data:
		return None
	return {
		"data": {
			"labels": [r["store"] for r in data[:15]],
			"datasets": [
				{"name": _("Buyback"), "values": [r["buyback_count"] for r in data[:15]]},
				{"name": _("Exchange"), "values": [r["exchange_count"] for r in data[:15]]},
			],
		},
		"type": "bar",
		"colors": ["#5e64ff", "#ff5858"],
	}
