# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
# R10 — Customer Approval Pending
# Inspected orders waiting for customer approval, with aging buckets.

import frappe
from frappe import _

from buyback.buyback.report.report_utils import (
	date_condition,
	standard_conditions,
	sla_minutes,
	aging_bucket_case,
)
from buyback.buyback.constants import PENDING_APPROVAL_STATUSES, AGING_BUCKETS_MINUTES


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{"fieldname": "name", "label": _("Order"), "fieldtype": "Link", "options": "Buyback Order", "width": 180},
		{"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "Warehouse", "width": 160},
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link", "options": "Customer", "width": 160},
		{"fieldname": "item", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 180},
		{"fieldname": "final_price", "label": _("Final Price"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 160},
		{"fieldname": "inspection_completed_at", "label": _("Inspection Done"), "fieldtype": "Datetime", "width": 160},
		{"fieldname": "age_minutes", "label": _("Age (min)"), "fieldtype": "Float", "width": 100, "precision": 1},
		{"fieldname": "aging_bucket", "label": _("Aging Bucket"), "fieldtype": "Data", "width": 120},
	]


def get_data(filters):
	dc = date_condition("o.creation", filters)
	sc = standard_conditions(filters, alias="o.")
	pending_in = ", ".join("'{}'".format(s) for s in PENDING_APPROVAL_STATUSES)
	age_expr = sla_minutes("COALESCE(bi.inspection_completed_at, o.creation)", "NOW()")
	bucket_expr = aging_bucket_case(age_expr, AGING_BUCKETS_MINUTES)

	rows = frappe.db.sql("""
		SELECT
			o.name,
			o.store,
			o.customer,
			o.item,
			o.final_price,
			o.status,
			bi.inspection_completed_at,
			{age_expr} AS age_minutes,
			{bucket_expr} AS aging_bucket
		FROM `tabBuyback Order` o
		LEFT JOIN `tabBuyback Inspection` bi ON bi.name = o.buyback_inspection
		WHERE {dc} {sc}
			AND (
				o.status IN ({pending_in})
				OR (o.customer_approved = 0 AND o.status = 'Approved')
			)
		ORDER BY age_minutes DESC
	""".format(
		age_expr=age_expr,
		bucket_expr=bucket_expr,
		dc=dc,
		sc=sc,
		pending_in=pending_in,
	), as_dict=True)

	return rows
