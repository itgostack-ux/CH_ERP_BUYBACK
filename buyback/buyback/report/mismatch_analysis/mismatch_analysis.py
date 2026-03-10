# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
# R9 — Mismatch Analysis
# Question-level customer vs inspector answer comparison.
# Grouped by question_code with avg price impact.

import frappe
from frappe import _

from buyback.buyback.report.report_utils import date_condition, standard_conditions


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{"fieldname": "question_code", "label": _("Question Code"), "fieldtype": "Data", "width": 140},
		{"fieldname": "question", "label": _("Question"), "fieldtype": "Link", "options": "Buyback Question Bank", "width": 240},
		{"fieldname": "total_compared", "label": _("Total Compared"), "fieldtype": "Int", "width": 120},
		{"fieldname": "mismatch_count", "label": _("Mismatches"), "fieldtype": "Int", "width": 100},
		{"fieldname": "mismatch_pct", "label": _("Mismatch %"), "fieldtype": "Percent", "width": 110},
		{"fieldname": "top_customer_answer", "label": _("Top Customer Ans"), "fieldtype": "Data", "width": 160},
		{"fieldname": "top_inspector_answer", "label": _("Top Inspector Ans"), "fieldtype": "Data", "width": 160},
		{"fieldname": "avg_price_impact", "label": _("Avg Price Impact"), "fieldtype": "Currency", "width": 130},
	]


def get_data(filters):
	dc = date_condition("bi.creation", filters)
	sc = standard_conditions(filters, alias="bi.")

	rows = frappe.db.sql("""
		SELECT
			ic.question_code,
			ic.question,
			COUNT(*) AS total_compared,
			SUM(CASE WHEN ic.match_status = 'Mismatch' THEN 1 ELSE 0 END) AS mismatch_count,
			ROUND(
				SUM(CASE WHEN ic.match_status = 'Mismatch' THEN 1 ELSE 0 END) / COUNT(*) * 100
			, 1) AS mismatch_pct,
			ROUND(AVG(COALESCE(ic.price_impact_difference, 0)), 2) AS avg_price_impact
		FROM `tabBuyback Inspection Comparison` ic
		INNER JOIN `tabBuyback Inspection` bi ON bi.name = ic.parent
		WHERE {dc} {sc}
		GROUP BY ic.question_code, ic.question
		ORDER BY mismatch_pct DESC
	""".format(dc=dc, sc=sc), as_dict=True)

	# ── Top customer/inspector answers per question (sub-query) ──
	if rows:
		question_codes = [r.question_code for r in rows if r.question_code]
		if question_codes:
			escaped_codes = ", ".join(frappe.db.escape(c) for c in question_codes)

			# Top customer answer per question_code
			cust_answers = frappe.db.sql("""
				SELECT question_code, customer_answer, COUNT(*) AS cnt
				FROM `tabBuyback Inspection Comparison`
				WHERE match_status = 'Mismatch'
					AND question_code IN ({codes})
				GROUP BY question_code, customer_answer
				ORDER BY question_code, cnt DESC
			""".format(codes=escaped_codes), as_dict=True)

			# Top inspector answer per question_code
			insp_answers = frappe.db.sql("""
				SELECT question_code, inspector_answer, COUNT(*) AS cnt
				FROM `tabBuyback Inspection Comparison`
				WHERE match_status = 'Mismatch'
					AND question_code IN ({codes})
				GROUP BY question_code, inspector_answer
				ORDER BY question_code, cnt DESC
			""".format(codes=escaped_codes), as_dict=True)

			# Build lookup: first occurrence per question_code is top answer
			cust_top = {}
			for r in cust_answers:
				if r.question_code not in cust_top:
					cust_top[r.question_code] = r.customer_answer

			insp_top = {}
			for r in insp_answers:
				if r.question_code not in insp_top:
					insp_top[r.question_code] = r.inspector_answer

			for r in rows:
				r["top_customer_answer"] = cust_top.get(r.question_code, "")
				r["top_inspector_answer"] = insp_top.get(r.question_code, "")

	return rows
