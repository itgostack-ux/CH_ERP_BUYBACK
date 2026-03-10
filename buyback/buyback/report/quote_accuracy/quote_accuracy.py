# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
# R7 — Quote Accuracy
# Shows estimated vs final price variance per order.
# Filter: variance_threshold (default 10%).

import frappe
from frappe import _

from buyback.buyback.report.report_utils import date_condition, standard_conditions


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{"fieldname": "name", "label": _("Order"), "fieldtype": "Link", "options": "Buyback Order", "width": 180},
		{"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "Warehouse", "width": 160},
		{"fieldname": "item", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 180},
		{"fieldname": "brand", "label": _("Brand"), "fieldtype": "Link", "options": "Brand", "width": 110},
		{"fieldname": "source", "label": _("Source"), "fieldtype": "Data", "width": 120},
		{"fieldname": "estimated_price", "label": _("Estimated Price"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "final_price", "label": _("Final Price"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "variance_amount", "label": _("Variance Amt"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "variance_pct", "label": _("Variance %"), "fieldtype": "Percent", "width": 110},
		{"fieldname": "condition_grade", "label": _("Grade"), "fieldtype": "Data", "width": 80},
	]


def get_data(filters):
	dc = date_condition("o.creation", filters)
	sc = standard_conditions(filters, alias="o.")
	threshold = float((filters or {}).get("variance_threshold", 10))

	rows = frappe.db.sql("""
		SELECT
			o.name,
			o.store,
			o.item,
			o.brand,
			a.source,
			COALESCE(a.estimated_price, a.quoted_price, 0) AS estimated_price,
			o.final_price,
			ROUND(o.final_price - COALESCE(a.estimated_price, a.quoted_price, 0), 2) AS variance_amount,
			ROUND(
				CASE WHEN COALESCE(a.estimated_price, a.quoted_price, 0) != 0
				THEN (o.final_price - COALESCE(a.estimated_price, a.quoted_price, 0))
					/ COALESCE(a.estimated_price, a.quoted_price, 1) * 100
				ELSE 0 END
			, 1) AS variance_pct,
			o.condition_grade
		FROM `tabBuyback Order` o
		INNER JOIN `tabBuyback Assessment` a ON a.name = o.buyback_assessment
		WHERE {dc} {sc}
			AND o.buyback_assessment IS NOT NULL AND o.buyback_assessment != ''
		HAVING ABS(variance_pct) >= {threshold}
		ORDER BY ABS(variance_pct) DESC
	""".format(
		dc=dc,
		sc=sc,
		threshold=threshold,
	), as_dict=True)

	return rows
