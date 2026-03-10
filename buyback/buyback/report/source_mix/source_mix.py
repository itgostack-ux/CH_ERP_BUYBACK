# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
# R2 — Source Mix
# Daily/weekly/monthly trend of App Diagnosis vs Store Manual assessments.
# Line chart: app vs manual over time.

import frappe
from frappe import _

from buyback.buyback.report.report_utils import date_condition, standard_conditions
from buyback.buyback.constants import SOURCE_APP, SOURCE_MANUAL


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)
	return columns, data, None, chart


def get_columns():
	return [
		{"fieldname": "period", "label": _("Period"), "fieldtype": "Data", "width": 140},
		{"fieldname": "app_count", "label": _("App Diagnosis"), "fieldtype": "Int", "width": 120},
		{"fieldname": "manual_count", "label": _("Store Manual"), "fieldtype": "Int", "width": 120},
		{"fieldname": "total", "label": _("Total"), "fieldtype": "Int", "width": 100},
		{"fieldname": "app_pct", "label": _("App %"), "fieldtype": "Percent", "width": 100},
	]


def get_data(filters):
	group_by = (filters or {}).get("group_by", "Daily")
	dc = date_condition("a.creation", filters)
	sc = standard_conditions(filters, alias="a.", field_map={"source": "source"})

	if group_by == "Weekly":
		period_expr = "DATE_FORMAT(a.creation, '%x-W%v')"
	elif group_by == "Monthly":
		period_expr = "DATE_FORMAT(a.creation, '%Y-%m')"
	else:
		period_expr = "DATE(a.creation)"

	rows = frappe.db.sql("""
		SELECT
			{period_expr} AS period,
			SUM(CASE WHEN a.source = {app} THEN 1 ELSE 0 END) AS app_count,
			SUM(CASE WHEN a.source = {manual} THEN 1 ELSE 0 END) AS manual_count,
			COUNT(*) AS total
		FROM `tabBuyback Assessment` a
		WHERE {dc} {sc}
		GROUP BY period
		ORDER BY period
	""".format(
		period_expr=period_expr,
		app=frappe.db.escape(SOURCE_APP),
		manual=frappe.db.escape(SOURCE_MANUAL),
		dc=dc,
		sc=sc,
	), as_dict=True)

	for r in rows:
		r["app_pct"] = round(r["app_count"] / r["total"] * 100, 1) if r["total"] else 0

	return rows


def get_chart(data):
	if not data:
		return None
	return {
		"data": {
			"labels": [r["period"] for r in data],
			"datasets": [
				{"name": _("App Diagnosis"), "values": [r["app_count"] for r in data]},
				{"name": _("Store Manual"), "values": [r["manual_count"] for r in data]},
			],
		},
		"type": "line",
		"colors": ["#5e64ff", "#ff5858"],
	}
