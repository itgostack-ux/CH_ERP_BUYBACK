# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
# R18 — Daily Operations Queue
# Actionable items for today, sorted by age. Priority based on aging.

import frappe
from frappe import _

from buyback.buyback.report.report_utils import (
	date_condition,
	standard_conditions,
	in_condition,
	sla_minutes,
)
from buyback.buyback.constants import (
	INSPECTION_OPEN_STATUSES,
	PENDING_PAYMENT_STATUSES,
)


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{"fieldname": "priority", "label": _("Priority"), "fieldtype": "Data", "width": 90},
		{"fieldname": "doctype", "label": _("Doctype"), "fieldtype": "Data", "width": 160},
		{"fieldname": "name", "label": _("Reference"), "fieldtype": "Dynamic Link", "options": "doctype", "width": 180},
		{"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "Warehouse", "width": 160},
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Data", "width": 160},
		{"fieldname": "item", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 180},
		{"fieldname": "action_needed", "label": _("Action Needed"), "fieldtype": "Data", "width": 200},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 140},
		{"fieldname": "age_minutes", "label": _("Age (min)"), "fieldtype": "Float", "width": 100, "precision": 1},
		{"fieldname": "owner", "label": _("Owner"), "fieldtype": "Link", "options": "User", "width": 160},
	]


def get_data(filters):
	sc_i = standard_conditions(filters, alias="i.")
	sc_o = standard_conditions(filters, alias="o.")
	insp_in = in_condition("i.status", INSPECTION_OPEN_STATUSES)
	pay_in = in_condition("o.status", PENDING_PAYMENT_STATUSES)
	age_i = sla_minutes("i.creation", "NOW()")
	age_o = sla_minutes("o.creation", "NOW()")

	priority_case = (
		"CASE"
		" WHEN {age} > 60 THEN 'High'"
		" WHEN {age} > 30 THEN 'Medium'"
		" ELSE 'Low'"
		" END"
	)

	rows = frappe.db.sql("""
		/* Inspections needing completion */
		SELECT
			{pri_i} AS priority,
			'Buyback Inspection' AS doctype,
			i.name,
			i.store,
			i.customer,
			i.item,
			'Complete Inspection' AS action_needed,
			i.status,
			{age_i} AS age_minutes,
			i.owner
		FROM `tabBuyback Inspection` i
		WHERE {insp_in} {sc_i}
			AND DATE(i.creation) >= CURDATE()

		UNION ALL

		/* Orders awaiting approval */
		SELECT
			{pri_o} AS priority,
			'Buyback Order' AS doctype,
			o.name,
			o.store,
			o.customer,
			o.item,
			'Approve Order' AS action_needed,
			o.status,
			{age_o} AS age_minutes,
			o.owner
		FROM `tabBuyback Order` o
		WHERE o.status = 'Awaiting Approval' {sc_o}
			AND DATE(o.creation) >= CURDATE()

		UNION ALL

		/* Orders awaiting customer approval */
		SELECT
			{pri_o} AS priority,
			'Buyback Order' AS doctype,
			o.name,
			o.store,
			o.customer,
			o.item,
			'Send Approval Link' AS action_needed,
			o.status,
			{age_o} AS age_minutes,
			o.owner
		FROM `tabBuyback Order` o
		WHERE o.status = 'Awaiting Customer Approval' {sc_o}
			AND DATE(o.creation) >= CURDATE()

		UNION ALL

		/* Orders pending payment */
		SELECT
			{pri_o} AS priority,
			'Buyback Order' AS doctype,
			o.name,
			o.store,
			o.customer,
			o.item,
			'Process Payment' AS action_needed,
			o.status,
			{age_o} AS age_minutes,
			o.owner
		FROM `tabBuyback Order` o
		WHERE {pay_in} {sc_o}
			AND o.status NOT IN ('Awaiting Approval', 'Awaiting Customer Approval')
			AND DATE(o.creation) >= CURDATE()

		ORDER BY age_minutes DESC
	""".format(
		pri_i=priority_case.format(age=age_i),
		pri_o=priority_case.format(age=age_o),
		age_i=age_i,
		age_o=age_o,
		insp_in=insp_in,
		pay_in=pay_in,
		sc_i=sc_i,
		sc_o=sc_o,
	), as_dict=True)

	return rows
