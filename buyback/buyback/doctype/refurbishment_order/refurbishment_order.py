from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from buyback.utils import (
	assert_buyback_scope,
	get_int_setting,
	is_privileged_user,
	require_configured_role,
)


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
	def before_insert(self):
		require_configured_role(
			"refurbishment_creation_roles", action=_("create Refurbishment Orders")
		)
		self.status = "Received"
		self.resulting_disposition = None
		self.resulting_stock_entry = None

	def validate(self):
		if not self.company:
			frappe.throw(_("Company is required"))
		if not self.item_code:
			frappe.throw(_("Item Code is required"))
		if flt(self.qty) <= 0:
			frappe.throw(_("Qty must be greater than zero"))
		self._validate_source_context()
		self._validate_warehouse_context()
		self._validate_status_transition()
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
			frappe.throw(_("A Restocked order must reference its server-created Stock Entry."))

	def _validate_status_transition(self):
		previous = self.get_doc_before_save()
		if not previous:
			return
		status_changed = previous.status != self.status
		result_changed = (
			previous.resulting_disposition != self.resulting_disposition
			or previous.resulting_stock_entry != self.resulting_stock_entry
		)
		if (status_changed or result_changed) and not self.flags.get("ch_authorized_status_transition"):
			frappe.throw(
				_("Refurbishment status and stock results can only be changed through authorised actions."),
				frappe.PermissionError,
			)

	def _validate_source_context(self):
		if not self.return_invoice and not self.service_request:
			if self.is_new():
				frappe.throw(_("A submitted Return Invoice or Service Request is required."))
			return
		if self.return_invoice and self.service_request:
			frappe.throw(_("Choose either Return Invoice or Service Request, not both."))

		if self.return_invoice:
			frappe.db.get_value("Sales Invoice", self.return_invoice, "name", for_update=True)
			source = frappe.get_doc("Sales Invoice", self.return_invoice)
			if not is_privileged_user():
				source.check_permission("read")
			if source.docstatus != 1 or not source.is_return:
				frappe.throw(_("Return Invoice must be a submitted customer return."))
			if self.company and self.company != source.company:
				frappe.throw(_("Company must match the Return Invoice."), frappe.PermissionError)
			if self.customer and self.customer != source.customer:
				frappe.throw(_("Customer must match the Return Invoice."), frappe.PermissionError)
			if self.original_invoice and self.original_invoice != source.return_against:
				frappe.throw(_("Original Invoice must match the Return Invoice."), frappe.PermissionError)
			self.company = source.company
			self.customer = source.customer
			self.original_invoice = source.return_against
			matching = [row for row in source.items if row.item_code == self.item_code]
			if self.serial_no:
				matching = [
					row
					for row in matching
					if self.serial_no
					in {
						token.strip()
						for token in (row.serial_no or "").replace(",", "\n").splitlines()
						if token.strip()
					}
				]
			if not matching:
				frappe.throw(_("Item/serial is not present on the Return Invoice."), frappe.PermissionError)
			warehouses = {row.warehouse for row in matching if row.warehouse}
			if not self.source_warehouse and len(warehouses) == 1:
				self.source_warehouse = next(iter(warehouses))
			if not self.source_warehouse or self.source_warehouse not in warehouses:
				frappe.throw(_("Source Warehouse must match the returned item row."), frappe.PermissionError)
			if flt(self.qty) > sum(abs(flt(row.qty)) for row in matching):
				frappe.throw(_("Qty exceeds the returned quantity."))
		else:
			frappe.db.get_value("Service Request", self.service_request, "name", for_update=True)
			source = frappe.get_doc("Service Request", self.service_request)
			if not is_privileged_user():
				source.check_permission("read")
			if source.docstatus == 2 or source.get("status") == "Cancelled":
				frappe.throw(_("Cancelled Service Requests cannot create refurbishment stock."))
			for fieldname in ("company", "customer"):
				expected = source.get(fieldname)
				if self.get(fieldname) and expected and self.get(fieldname) != expected:
					frappe.throw(_("{0} must match the Service Request.").format(fieldname.title()), frappe.PermissionError)
				if expected:
					self.set(fieldname, expected)

	def _validate_warehouse_context(self):
		for fieldname in ("source_warehouse", "target_warehouse"):
			warehouse = self.get(fieldname)
			if not warehouse:
				continue
			company = frappe.db.get_value("Warehouse", warehouse, "company")
			if not company or company != self.company:
				frappe.throw(_("{0} must belong to the Refurbishment Order company.").format(self.meta.get_label(fieldname)))
			assert_buyback_scope(warehouse=warehouse, company=self.company)

	@frappe.whitelist(methods=["POST"])
	def advance_status(self, next_status: str):
		require_configured_role(
			"refurbishment_operation_roles", action=_("advance Refurbishment Orders")
		)
		self.check_permission("write")
		frappe.db.get_value(self.doctype, self.name, "name", for_update=True)
		self.reload()
		assert_buyback_scope(warehouse=self.source_warehouse, company=self.company)
		transitions = {
			"Received": {"Diagnosed", "Cancelled"},
			"Diagnosed": {"Repaired", "Graded", "Cancelled"},
			"Repaired": {"Graded", "Cancelled"},
			"Graded": {"Restocked", "Cancelled"},
		}
		next_status = (next_status or "").strip()
		if next_status not in transitions.get(self.status, set()):
			frappe.throw(_("Invalid Refurbishment transition: {0} to {1}.").format(self.status, next_status))
		if next_status == "Restocked":
			return self.restock()
		self.flags.ch_authorized_status_transition = True
		self.status = next_status
		self.save()
		return {"name": self.name, "status": self.status}

	@frappe.whitelist(methods=["POST"])
	def restock(self, target_warehouse: str | None = None):
		require_configured_role(
			"refurbishment_restock_roles", action=_("restock refurbished devices")
		)
		self.check_permission("write")
		frappe.db.get_value(self.doctype, self.name, "name", for_update=True)
		self.reload()
		if self.status != "Graded":
			frappe.throw(_("Only a Graded Refurbishment Order can be restocked."))
		self.target_warehouse = (target_warehouse or self.target_warehouse or _default_target_warehouse(self.company))
		self._validate_warehouse_context()
		self._require_data_wipe_before_restock()
		self._create_restock_disposition()
		if not self.resulting_stock_entry:
			frappe.throw(_("Restock did not create a Stock Entry."))
		self.flags.ch_authorized_status_transition = True
		self.status = "Restocked"
		self.save()
		return {
			"name": self.name,
			"status": self.status,
			"stock_entry": self.resulting_stock_entry,
		}

	def _require_data_wipe_before_restock(self):
		# Off-switch for pilots / legacy back-fills.
		gate_on = frappe.db.get_single_value(
			"Buyback Settings", "require_data_wipe_before_restock"
		)
		if gate_on is not None and not int(gate_on or 0):
			return

		# Trace back to a Buyback Order via the serial (auto-set by the
		# buyback lifecycle when the device was bought back).
		buyback_order = None
		if self.serial_no:
			buyback_order = frappe.db.get_value(
				"Serial No", self.serial_no, "ch_buyback_order"
			)

		# When there's no source Buyback Order we treat this refurb as a
		# regular sales return / vendor RMA — no data-wipe gate applies.
		if not buyback_order:
			return

		wiped = frappe.db.get_value(
			"Buyback Order", buyback_order, "data_wipe_certificate"
		)
		if not wiped:
			frappe.throw(
				_(
					"Refurbishment Order {0} sources a device from Buyback "
					"Order {1} but no submitted CH Data Wipe Certificate is on "
					"file. Record the wipe (Buyback Order → 'Record Data Wipe') "
					"before Restocking."
				).format(frappe.bold(self.name), frappe.bold(buyback_order)),
				title=_("Data Wipe Certificate Required"),
			)

		# Also gate against a revoked / cancelled certificate.
		certificate = frappe.db.get_value(
			"CH Data Wipe Certificate",
			wiped,
			["docstatus", "status", "wipe_verified", "verified_by"],
			as_dict=True,
		)
		if (
			not certificate
			or certificate.docstatus != 1
			or certificate.status != "Verified"
			or not certificate.wipe_verified
			or not certificate.verified_by
		):
			frappe.throw(
				_(
					"Data Wipe Certificate {0} for Buyback Order {1} is not in "
					"a submitted state. Re-run the wipe before Restocking."
				).format(frappe.bold(wiped), frappe.bold(buyback_order)),
				title=_("Data Wipe Certificate Not Submitted"),
			)

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


@frappe.whitelist(methods=["POST"])
def create_from_return(return_invoice: str, original_invoice: str | None = None, items: list | None = None,
		customer: str | None = None, company: str | None = None, physical_condition: str | None = None,
		return_reason: str | None = None, return_remarks: str | None = None) -> dict:
	require_configured_role(
		"refurbishment_creation_roles", action=_("create Refurbishment Orders")
	)
	frappe.has_permission("Refurbishment Order", ptype="create", throw=True)
	if not return_invoice:
		frappe.throw(_("A submitted return invoice is required."))
	frappe.db.get_value("Sales Invoice", return_invoice, "name", for_update=True)
	return_doc = frappe.get_doc("Sales Invoice", return_invoice)
	if not is_privileged_user():
		return_doc.check_permission("read")
	if return_doc.docstatus != 1 or not return_doc.is_return:
		frappe.throw(_("Sales Invoice {0} is not a submitted customer return.").format(return_invoice))
	if company and company != return_doc.company:
		frappe.throw(_("Company does not match the return invoice."), frappe.PermissionError)
	if customer and customer != return_doc.customer:
		frappe.throw(_("Customer does not match the return invoice."), frappe.PermissionError)
	if original_invoice and original_invoice != return_doc.return_against:
		frappe.throw(_("Original invoice does not match the return invoice."), frappe.PermissionError)

	if isinstance(items, str):
		items = frappe.parse_json(items)
	if not isinstance(items, list) or not items:
		frappe.throw(_("Select at least one returned item."))
	max_rows = get_int_setting("max_refurbishment_rows", 100)
	if len(items) > max_rows:
		frappe.throw(
			_("A maximum of {0} returned item rows can be processed at once.").format(max_rows)
		)
	existing_item_quantities: dict[str, float] = {}
	existing_serial_quantities: dict[tuple[str, str], float] = {}
	for row in frappe.get_all(
		"Refurbishment Order",
		filters={
			"return_invoice": return_invoice,
			"docstatus": ["<", 2],
			"status": ["!=", "Cancelled"],
		},
		fields=["item_code", "serial_no", "qty"],
	):
		row_qty = flt(row.qty)
		existing_item_quantities[row.item_code] = (
			existing_item_quantities.get(row.item_code, 0) + row_qty
		)
		if row.serial_no:
			key = (row.item_code, row.serial_no)
			existing_serial_quantities[key] = (
				existing_serial_quantities.get(key, 0) + row_qty
			)

	return_rows_by_item: dict[str, list] = {}
	serials_by_row: dict[str, set[str]] = {}
	for row in return_doc.items:
		return_rows_by_item.setdefault(row.item_code, []).append(row)
		serials_by_row[row.name] = {
			token.strip()
			for token in (row.serial_no or "").replace(",", "\n").splitlines()
			if token.strip()
		}

	created = []
	consumed: dict[tuple[str, str], float] = {}
	for item in items:
		if not isinstance(item, dict):
			frappe.throw(_("Every selected item must be an object."))
		item_code = (item.get("item_code") or "").strip()
		if not item_code:
			frappe.throw(_("Every selected row must include an Item Code."))
		requested_serial = (item.get("serial_no") or "").strip()
		raw_qty = item.get("qty")
		requested_qty = abs(flt(raw_qty if raw_qty is not None else 1))
		if requested_qty <= 0:
			frappe.throw(_("Requested quantity must be greater than zero."))
		item_rows = return_rows_by_item.get(item_code, [])
		matching_rows = item_rows
		if requested_serial:
			matching_rows = [
				row for row in matching_rows
				if requested_serial in serials_by_row.get(row.name, set())
			]
		if not matching_rows:
			frappe.throw(
				_("Item {0}{1} is not present on return invoice {2}.").format(
					item_code,
					_(" / serial {0}").format(requested_serial) if requested_serial else "",
					return_invoice,
				),
				frappe.PermissionError,
			)
		returned_qty = sum(abs(flt(row.qty)) for row in matching_rows)
		returned_item_qty = sum(abs(flt(row.qty)) for row in item_rows)
		quantity_key = (item_code, requested_serial)
		consumed[quantity_key] = consumed.get(quantity_key, 0) + requested_qty
		if consumed[quantity_key] > returned_qty:
			frappe.throw(
				_("Requested quantity for {0} exceeds the quantity on the return invoice.").format(item_code)
			)
		warehouses = {row.warehouse for row in matching_rows if row.warehouse}
		if not warehouses:
			frappe.throw(_("The returned item must have a source warehouse."))
		requested_warehouse = (item.get("warehouse") or "").strip()
		if requested_warehouse and requested_warehouse not in warehouses:
			frappe.throw(_("Source warehouse does not match the return invoice."), frappe.PermissionError)
		if not requested_warehouse and len(warehouses) > 1:
			frappe.throw(_("Select the source warehouse for an item returned to multiple warehouses."))
		source_warehouse = requested_warehouse or next(iter(sorted(warehouses)), None)
		assert_buyback_scope(warehouse=source_warehouse, company=return_doc.company)
		existing_key = (item_code, requested_serial)
		existing_qty = (
			existing_serial_quantities.get(existing_key, 0)
			if requested_serial
			else existing_item_quantities.get(item_code, 0)
		)
		if (
			(requested_serial and existing_qty)
			or existing_qty + requested_qty > returned_qty
			or existing_item_quantities.get(item_code, 0) + requested_qty > returned_item_qty
		):
			frappe.throw(
				_("A Refurbishment Order already exists for this returned item{0}.").format(
					_(" / serial {0}").format(requested_serial) if requested_serial else ""
				)
			)

		doc = frappe.get_doc({
			"doctype": "Refurbishment Order",
			"company": return_doc.company,
			"customer": return_doc.customer,
			"original_invoice": return_doc.return_against,
			"return_invoice": return_invoice,
			"item_code": item_code,
			"serial_no": requested_serial or None,
			"source_warehouse": source_warehouse,
			"qty": requested_qty,
			"physical_condition": physical_condition or "Damaged",
			"return_reason": return_reason,
			"return_remarks": return_remarks,
			"status": "Received",
		})
		doc.insert()
		created.append(doc.name)
		existing_item_quantities[item_code] = (
			existing_item_quantities.get(item_code, 0) + requested_qty
		)
		if requested_serial:
			existing_serial_quantities[existing_key] = existing_qty + requested_qty
	return {"orders": created}
