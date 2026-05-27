from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate


def _default_target_warehouse(company: str) -> str | None:
	rows = frappe.get_all(
		"Warehouse",
		filters={"company": company, "is_group": 0, "disabled": 0, "name": ["like", "Stores%"]},
		pluck="name",
		limit=1,
		order_by="name asc",
	)
	return rows[0] if rows else None


def _suggested_price(item_code: str, grade: str | None) -> float:
	base = frappe.db.get_value(
		"CH Item Price",
		{"item_code": item_code, "channel": "POS", "status": "Active"},
		"selling_price",
	)
	factor = 1
	if grade:
		factor = flt(frappe.db.get_value("Grade Master", grade, "price_factor") or 1)
	return flt(base) * (factor or 1)


class RefurbishmentOrder(Document):
	def validate(self):
		if not self.company:
			frappe.throw(_("Company is required"))
		if not self.item_code:
			frappe.throw(_("Item Code is required"))
		self.item_name = self.item_name or frappe.db.get_value("Item", self.item_code, "item_name")
		self.customer_name = self.customer_name or (frappe.db.get_value("Customer", self.customer, "customer_name") if self.customer else None)
		if not self.target_warehouse and self.status == "Restocked":
			self.target_warehouse = _default_target_warehouse(self.company)
		if self.grade:
			self.suggested_resale_price = _suggested_price(self.item_code, self.grade)
			grade_name = (frappe.db.get_value("Grade Master", self.grade, "grade_name") or "").strip().upper()[:1]
			if grade_name in ("A", "B"):
				self.expected_resale_type = "Refurbished"
			elif grade_name:
				self.expected_resale_type = "Pre-Owned"
		if self.status == "Restocked" and not self.resulting_stock_entry:
			self._create_restock_disposition()

	def _create_restock_disposition(self):
		if not frappe.db.exists("DocType", "CH Buyback Disposition"):
			return
		if not self.source_warehouse or not self.target_warehouse:
			frappe.throw(_("Source and Target Warehouse are required to restock"))
		doc = frappe.get_doc({
			"doctype": "CH Buyback Disposition",
			"disposition_date": nowdate(),
			"company": self.company,
			"source_warehouse": self.source_warehouse,
			"item_code": self.item_code,
			"serial_no": self.serial_no,
			"qty": self.qty or 1,
			"disposition": "Restock",
			"target_warehouse": self.target_warehouse,
			"notes": _("Auto-created from Refurbishment Order {0}").format(self.name),
		})
		doc.insert(ignore_permissions=True)
		doc.submit()
		self.resulting_disposition = doc.name
		self.resulting_stock_entry = doc.resulting_stock_entry


@frappe.whitelist()
def create_from_return(return_invoice: str, original_invoice: str | None = None, items: list | None = None,
		customer: str | None = None, company: str | None = None, physical_condition: str | None = None,
		return_reason: str | None = None, return_remarks: str | None = None) -> dict:
	items = items or []
	created = []
	for item in items:
		if not item.get("item_code"):
			continue
		doc = frappe.get_doc({
			"doctype": "Refurbishment Order",
			"company": company,
			"customer": customer,
			"original_invoice": original_invoice,
			"return_invoice": return_invoice,
			"item_code": item.get("item_code"),
			"serial_no": item.get("serial_no"),
			"source_warehouse": item.get("warehouse"),
			"qty": flt(item.get("qty") or 1),
			"physical_condition": physical_condition or "Damaged",
			"return_reason": return_reason,
			"return_remarks": return_remarks,
			"status": "Received",
		})
		doc.insert(ignore_permissions=True)
		created.append(doc.name)
	return {"orders": created}
