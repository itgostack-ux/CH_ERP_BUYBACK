"""Frappe Migration Patch: Add performance indices for Stock Entry bulk imports.

These composite indices speed up bulk imports:
1. company + posting_date (filtering by date range)
2. docstatus + purpose (status/purpose lookups)
3. company + purpose (bulk operations by purpose)

Also adds indices on Stock Entry Detail child table for item/batch lookups.
Plus critical indices on:
- Batch (item_code, disabled, expiry_date) — fast batch lookups during validation
- Item Default (parent, company) — fast item default lookups

Idempotent: Uses IF NOT EXISTS, safe to run multiple times.
Automatically runs on: bench migrate
"""

import frappe


def execute():
	"""Frappe migration patch - adds performance indices for bulk imports."""
	
	db = frappe.db
	
	# Indices for Stock Entry parent table
	stock_entry_indices = [
		("idx_company_posting_date", "`company`, `posting_date`"),
		("idx_status_purpose", "`docstatus`, `purpose`"),
		("idx_company_purpose", "`company`, `purpose`"),
	]
	
	# Indices for Stock Entry Detail child table
	stock_entry_detail_indices = [
		("idx_parent_item", "`parent`, `item_code`"),
		("idx_batch_no", "`batch_no`"),
		("idx_warehouse", "`s_warehouse`, `t_warehouse`"),
	]
	
	# Indices for Batch table (used during Stock Entry validation)
	batch_indices = [
		("idx_item_disabled", "`item`, `disabled`"),
		("idx_disabled_expiry", "`disabled`, `expiry_date`"),
	]
	
	# Indices for Item Default table (6K+ records, looked up by parent+company)
	item_default_indices = [
		("idx_parent_company", "`parent`, `company`"),
	]
	
	# Create Stock Entry parent table indices
	for idx_name, columns in stock_entry_indices:
		try:
			db.sql(f"ALTER TABLE `tabStock Entry` ADD INDEX IF NOT EXISTS `{idx_name}` ({columns})")
			frappe.logger().info(f"✅ Stock Entry index created: {idx_name}")
		except Exception as e:
			frappe.logger().warning(f"Stock Entry index {idx_name}: {str(e)}")
	
	# Create Stock Entry Detail child table indices
	for idx_name, columns in stock_entry_detail_indices:
		try:
			db.sql(f"ALTER TABLE `tabStock Entry Detail` ADD INDEX IF NOT EXISTS `{idx_name}` ({columns})")
			frappe.logger().info(f"✅ Stock Entry Detail index created: {idx_name}")
		except Exception as e:
			frappe.logger().warning(f"Stock Entry Detail index {idx_name}: {str(e)}")
	
	# Create Batch table indices
	for idx_name, columns in batch_indices:
		try:
			db.sql(f"ALTER TABLE `tabBatch` ADD INDEX IF NOT EXISTS `{idx_name}` ({columns})")
			frappe.logger().info(f"✅ Batch index created: {idx_name}")
		except Exception as e:
			frappe.logger().warning(f"Batch index {idx_name}: {str(e)}")
	
	# Create Item Default table indices
	for idx_name, columns in item_default_indices:
		try:
			db.sql(f"ALTER TABLE `tabItem Default` ADD INDEX IF NOT EXISTS `{idx_name}` ({columns})")
			frappe.logger().info(f"✅ Item Default index created: {idx_name}")
		except Exception as e:
			frappe.logger().warning(f"Item Default index {idx_name}: {str(e)}")
	
	frappe.logger().info("Stock Entry performance indices migration complete")
