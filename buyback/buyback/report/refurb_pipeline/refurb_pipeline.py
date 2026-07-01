import frappe
from frappe import _
from buyback.buyback.report.report_utils import scope_condition


def execute(filters=None):
	filters = filters or {}
	columns = [
		{"fieldname": "name", "label": _("Refurb Order"), "fieldtype": "Link", "options": "Refurbishment Order", "width": 150},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 110},
		{"fieldname": "company", "label": _("Company"), "fieldtype": "Link", "options": "Company", "width": 180},
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 160},
		{"fieldname": "serial_no", "label": _("IMEI / Serial"), "fieldtype": "Data", "width": 160},
		{"fieldname": "grade", "label": _("Grade"), "fieldtype": "Link", "options": "Grade Master", "width": 100},
		{"fieldname": "expected_resale_type", "label": _("Resale Type"), "fieldtype": "Data", "width": 120},
		{"fieldname": "suggested_resale_price", "label": _("Suggested Price"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "return_invoice", "label": _("Return Invoice"), "fieldtype": "Link", "options": "Sales Invoice", "width": 150},
	]
	conditions = []
	values = {}
	if filters.get("from_date"):
		conditions.append("creation >= %(from_date)s")
		values["from_date"] = filters["from_date"]
	if filters.get("to_date"):
		conditions.append("creation < DATE_ADD(%(to_date)s, INTERVAL 1 DAY)")
		values["to_date"] = filters["to_date"]
	if filters.get("company"):
		conditions.append("company = %(company)s")
		values["company"] = filters["company"]
	if filters.get("status"):
		conditions.append("status = %(status)s")
		values["status"] = filters["status"]
	where_clause = " AND ".join(conditions) if conditions else "1=1"
	# Tier 4 — CH User Scope narrowing on source_warehouse (fail-closed).
	sc = scope_condition(warehouse_field="source_warehouse", store_field=None)
	if sc:
		where_clause = f"({where_clause}){sc}"
	data = frappe.db.sql(f"SELECT name, status, company, item_code, serial_no, grade, expected_resale_type, suggested_resale_price, return_invoice FROM `tabRefurbishment Order` WHERE {where_clause} ORDER BY modified DESC", values, as_dict=True)
	summary = []
	if data:
		status_map = {}
		for row in data:
			status_map[row.status] = status_map.get(row.status, 0) + 1
		summary = [{"label": _(k), "value": v, "datatype": "Int"} for k, v in status_map.items()]
	return columns, data, None, None, summary
