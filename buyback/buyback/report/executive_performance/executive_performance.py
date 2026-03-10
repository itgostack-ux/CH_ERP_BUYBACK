# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
# R4 — Executive Performance
# Per-user (owner) metrics across quotes and orders.
# Bar chart: top 10 executives by conversion.

import frappe
from frappe import _

from buyback.buyback.report.report_utils import (
	date_condition,
	standard_conditions,
	in_condition,
	sla_minutes,
)
from buyback.buyback.constants import PAID_STATUSES, SOURCE_APP


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)
	return columns, data, None, chart


def get_columns():
	return [
		{"fieldname": "executive", "label": _("Executive"), "fieldtype": "Link", "options": "User", "width": 200},
		{"fieldname": "quotes_handled", "label": _("Quotes"), "fieldtype": "Int", "width": 90},
		{"fieldname": "assessments_handled", "label": _("Assessments"), "fieldtype": "Int", "width": 110},
		{"fieldname": "orders_created", "label": _("Orders"), "fieldtype": "Int", "width": 90},
		{"fieldname": "settled_count", "label": _("Settled"), "fieldtype": "Int", "width": 90},
		{"fieldname": "conversion_pct", "label": _("Conversion %"), "fieldtype": "Percent", "width": 110},
		{"fieldname": "avg_deal_value", "label": _("Avg Deal Value"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "app_quote_pct", "label": _("App Quote %"), "fieldtype": "Percent", "width": 110},
		{"fieldname": "avg_tat_minutes", "label": _("Avg TAT (min)"), "fieldtype": "Float", "width": 110, "precision": 1},
	]


def get_data(filters):
	dc_o = date_condition("o.creation", filters)
	dc_q = date_condition("q.creation", filters)
	dc_a = date_condition("a.creation", filters)
	sc_o = standard_conditions(filters, alias="o.")
	sc_q = standard_conditions(filters, alias="q.", field_map={"source": "source"})
	sc_a = standard_conditions(filters, alias="a.")
	paid_in = in_condition("o.status", PAID_STATUSES)

	# ── Orders by owner ──
	order_data = frappe.db.sql("""
		SELECT
			o.owner AS executive,
			COUNT(*) AS orders_created,
			SUM(CASE WHEN {paid_in} THEN 1 ELSE 0 END) AS settled_count,
			ROUND(COALESCE(AVG(CASE WHEN {paid_in} THEN o.final_price END), 0), 2) AS avg_deal_value,
			ROUND(AVG({tat}), 1) AS avg_tat_minutes
		FROM `tabBuyback Order` o
		WHERE {dc} {sc}
		GROUP BY o.owner
	""".format(
		paid_in=paid_in,
		tat=sla_minutes("o.creation", "o.approval_date"),
		dc=dc_o,
		sc=sc_o,
	), as_dict=True)

	user_map = {}
	for r in order_data:
		user_map[r.executive] = r

	# ── Quotes by owner (now reads from Assessment) ──
	for r in frappe.db.sql("""
		SELECT
			q.owner,
			COUNT(*) AS cnt,
			SUM(CASE WHEN q.source = {app} THEN 1 ELSE 0 END) AS app_count
		FROM `tabBuyback Assessment` q
		WHERE {dc} {sc}
		GROUP BY q.owner
	""".format(dc=dc_q, sc=sc_q, app=frappe.db.escape(SOURCE_APP)), as_dict=True):
		s = user_map.setdefault(r.owner, {"executive": r.owner})
		s["quotes_handled"] = r.cnt
		s["_app_quotes"] = r.app_count

	# ── Assessments by owner ──
	for r in frappe.db.sql("""
		SELECT a.owner, COUNT(*) AS cnt
		FROM `tabBuyback Assessment` a
		WHERE {dc} {sc}
		GROUP BY a.owner
	""".format(dc=dc_a, sc=sc_a), as_dict=True):
		user_map.setdefault(r.owner, {"executive": r.owner})["assessments_handled"] = r.cnt

	# ── Build result ──
	data = []
	for user, r in user_map.items():
		quotes = r.get("quotes_handled", 0)
		orders = r.get("orders_created", 0)
		data.append({
			"executive": user,
			"quotes_handled": quotes,
			"assessments_handled": r.get("assessments_handled", 0),
			"orders_created": orders,
			"settled_count": r.get("settled_count", 0),
			"conversion_pct": round(orders / quotes * 100, 1) if quotes else 0,
			"avg_deal_value": r.get("avg_deal_value", 0),
			"app_quote_pct": round(r.get("_app_quotes", 0) / quotes * 100, 1) if quotes else 0,
			"avg_tat_minutes": r.get("avg_tat_minutes", 0),
		})

	data.sort(key=lambda x: x["conversion_pct"], reverse=True)
	return data


def get_chart(data):
	if not data:
		return None
	top = data[:10]
	return {
		"data": {
			"labels": [r["executive"] for r in top],
			"datasets": [{"name": _("Conversion %"), "values": [r["conversion_pct"] for r in top]}],
		},
		"type": "bar",
		"colors": ["#5e64ff"],
	}
