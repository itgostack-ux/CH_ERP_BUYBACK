# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
# R12 — Pending Settlement
# Approved but not yet paid/settled orders with aging.

import frappe
from frappe import _

from buyback.buyback.report.report_utils import (
	date_condition,
	standard_conditions,
	in_condition,
	sla_minutes,
	aging_bucket_case,
)
from buyback.buyback.constants import PENDING_PAYMENT_STATUSES, AGING_BUCKETS_HOURS


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
		{"fieldname": "settlement_type", "label": _("Settlement Type"), "fieldtype": "Data", "width": 120},
		{"fieldname": "approval_date", "label": _("Approval Date"), "fieldtype": "Datetime", "width": 160},
		{"fieldname": "customer_approved_at", "label": _("Customer Approved At"), "fieldtype": "Datetime", "width": 160},
		{"fieldname": "age_hours", "label": _("Age (hrs)"), "fieldtype": "Float", "width": 100, "precision": 1},
		{"fieldname": "aging_bucket", "label": _("Aging Bucket"), "fieldtype": "Data", "width": 120},
		{"fieldname": "owner", "label": _("Owner"), "fieldtype": "Link", "options": "User", "width": 160},
	]


def get_data(filters):
	dc = date_condition("o.creation", filters)
	sc = standard_conditions(filters, alias="o.")
	pending_in = in_condition("o.status", PENDING_PAYMENT_STATUSES)
	age_hours_expr = "ROUND(TIMESTAMPDIFF(SECOND, COALESCE(o.approval_date, o.creation), NOW()) / 3600, 1)"
	bucket_expr = aging_bucket_case(age_hours_expr, AGING_BUCKETS_HOURS)

	rows = frappe.db.sql("""
		SELECT
			o.name,
			o.store,
			o.customer,
			o.item,
			o.final_price,
			o.settlement_type,
			o.approval_date,
			o.customer_approved_at,
			{age_hours} AS age_hours,
			{bucket} AS aging_bucket,
			o.owner
		FROM `tabBuyback Order` o
		WHERE {dc} {sc}
			AND {pending_in}
		ORDER BY age_hours DESC
	""".format(
		age_hours=age_hours_expr,
		bucket=bucket_expr,
		dc=dc,
		sc=sc,
		pending_in=pending_in,
	), as_dict=True)

	return rows
