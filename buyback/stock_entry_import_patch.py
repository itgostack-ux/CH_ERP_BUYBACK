"""Patch: Integrate Stock Entry import optimizations into validate() flow.

This patch modifies Stock Entry validation to:
1. Skip expensive rate calculations for simple transfers during import
2. Use cached batch/item lookups instead of N+1 queries
3. Respect frappe.flags.in_import context

Hook into Stock Entry validate() to call optimizations.
"""

from frappe.utils import cint
import frappe


def patch_stock_entry_validate():
    """Apply runtime optimizations to Stock Entry.validate()."""
    
    # Import the original class
    from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry
    from erpnext.stock.doctype.stock_entry.stock_entry_import_optimize import (
        should_optimize_import,
        optimize_validate_item,
        optimize_validate_batch,
        should_skip_valuation_rate_for_import,
        StockEntryImportCache,
    )
    
    # Store original methods
    original_validate = StockEntry.validate
    original_calculate_rate = StockEntry.calculate_rate_and_amount
    
    def patched_validate(self):
        """Optimized validate() that uses batch queries during import."""
        # Call original validation but with optimizations
        
        self.pro_doc = frappe._dict()
        if self.work_order:
            self.pro_doc = frappe.get_doc("Work Order", self.work_order)
        
        self.validate_duplicate_serial_and_batch_bundle("items")
        self.validate_posting_time()
        self.validate_purpose()
        
        # OPTIMIZATION: Use batch queries if in bulk import
        if should_optimize_import():
            if not optimize_validate_item(self):
                self.validate_item()
        else:
            self.validate_item()
        
        self.validate_customer_provided_item()
        self.set_transfer_qty()
        self.validate_uom_is_integer("uom", "qty")
        self.validate_uom_is_integer("stock_uom", "transfer_qty")
        self.validate_warehouse_of_sabb()
        self.validate_work_order()
        self.validate_source_stock_entry()
        self.validate_bom()
        self.set_process_loss_qty()
        self.validate_purchase_order()
        self.validate_company_in_accounting_dimension()
        
        if self.purpose in ("Manufacture", "Repack"):
            self.mark_finished_and_secondary_items()
            if not self.job_card:
                self.validate_finished_goods()
            else:
                self.validate_job_card_fg_item()
        
        self.validate_warehouse()
        self.validate_with_material_request()
        
        if self.purpose == "Disassemble":
            self.validate_disassembly_quantities()
        
        # OPTIMIZATION: Use batch queries if in bulk import
        if should_optimize_import():
            if not optimize_validate_batch(self):
                self.validate_batch()
        else:
            self.validate_batch()
        
        self.validate_inspection()
        self.validate_fg_completed_qty()
        self.validate_difference_account()
        self.set_job_card_data()
        self.validate_job_card_item()
        self.set_purpose_for_stock_entry()
        self.clean_serial_nos()
        self.validate_repack_entry()
        
        if not self.from_bom:
            self.fg_completed_qty = 0.0
        
        self.make_serial_and_batch_bundle_for_outward()
        self.validate_serialized_batch()
        
        # OPTIMIZATION: Skip expensive rate calc for simple transfers during import
        if should_optimize_import() and should_skip_valuation_rate_for_import(self):
            # Set zero rates to avoid stock ledger scans
            for item in self.get("items"):
                item.basic_rate = 0.0
                item.basic_amount = 0.0
        else:
            self.calculate_rate_and_amount()
        
        self.validate_putaway_capacity()
        self.validate_component_and_quantities()
        self.validate_finished_good_serial_batch_for_work_order()
        self.validate_inventory_dimension_mandatory()
        
        if self.get("purpose") != "Manufacture":
            self.reset_default_field_value("from_warehouse", "items", "s_warehouse")
            self.reset_default_field_value("to_warehouse", "items", "t_warehouse")
        
        self.validate_same_source_target_warehouse_during_material_transfer()
        self.validate_closed_subcontracting_order()
        self.validate_subcontract_order()
        self.validate_raw_materials_exists()
        super(StockEntry, self).validate_subcontracting_inward()
    
    # Patch the class method
    StockEntry.validate = patched_validate
    
    frappe.logger().info("✅ Stock Entry import optimizations patched")


# Execute on module load
try:
    patch_stock_entry_validate()
except Exception as e:
    frappe.logger().error(f"Failed to patch Stock Entry: {str(e)}")
