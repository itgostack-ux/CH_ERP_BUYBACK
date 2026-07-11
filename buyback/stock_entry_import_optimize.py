"""Stock Entry bulk import optimization — speeds up 19K+ record loads.

Problem: Stock Entry validation loops call expensive queries per-row:
- validate_item() → get_item_details() [LEFT JOIN per item]
- validate_batch() → frappe.db.get_value() per batch [N+1 queries]
- calculate_rate_and_amount() → get_valuation_rate() per item [stock ledger scan]

Solution: Cache lookups, batch queries, and skip non-critical validations during import.
Cache is thread-safe and respects frappe.flags.in_import context.
"""

import frappe
from frappe.utils import cint, getdate
from erpnext.stock.get_item_details import ItemDetailsCtx


class StockEntryImportCache:
    """Thread-local cache for Stock Entry bulk imports."""
    
    def __init__(self):
        self.item_details_cache = {}  # {(item_code, company): item_dict}
        self.batch_cache = {}  # {(batch_no): {disabled, expiry_date}}
        self.warehouse_cache = {}  # {warehouse_name: warehouse_doc}
    
    @classmethod
    def get(cls):
        """Get or create cache for current thread/context."""
        if not hasattr(frappe.local, 'se_import_cache'):
            frappe.local.se_import_cache = cls()
        return frappe.local.se_import_cache
    
    @classmethod
    def clear(cls):
        """Clear cache (called after import completes)."""
        if hasattr(frappe.local, 'se_import_cache'):
            del frappe.local.se_import_cache


def should_optimize_import():
    """Check if we're in bulk import context."""
    return cint(frappe.flags.get('in_import', 0))


def get_item_details_batch(item_codes, company):
    """Batch query to fetch item details instead of per-row queries.
    
    Returns dict: {item_code: {stock_uom, description, ...}}
    """
    if not item_codes:
        return {}
    
    cache = StockEntryImportCache.get()
    uncached = [code for code in item_codes if (code, company) not in cache.item_details_cache]
    
    if uncached:
        # Single query for all uncached items instead of N queries
        item = frappe.qb.DocType("Item")
        item_default = frappe.qb.DocType("Item Default")
        
        from frappe.utils import nowdate
        
        query = (
            frappe.qb.from_(item)
            .left_join(item_default)
            .on((item.name == item_default.parent) & (item_default.company == company))
            .select(
                item.name,
                item.stock_uom,
                item.description,
                item.image,
                item.item_name,
                item.item_group,
                item.has_batch_no,
                item.sample_quantity,
                item.has_serial_no,
                item.allow_alternative_item,
                item_default.expense_account,
                item_default.buying_cost_center,
            )
            .where(
                (item.name.isin(uncached))
                & (item.disabled == 0)
                & (
                    (item.end_of_life.isnull())
                    | (item.end_of_life < "1900-01-01")
                    | (item.end_of_life > nowdate())
                )
            )
        )
        
        results = query.run(as_dict=True)
        for row in results:
            cache.item_details_cache[(row['name'], company)] = row
    
    return {
        code: cache.item_details_cache.get((code, company))
        for code in item_codes
    }


def get_batch_details_batch(batch_nos):
    """Batch query for batch validation instead of per-batch queries.
    
    Returns dict: {batch_no: {disabled, expiry_date}}
    """
    if not batch_nos:
        return {}
    
    batch_nos = [b for b in batch_nos if b]  # Filter None
    if not batch_nos:
        return {}
    
    cache = StockEntryImportCache.get()
    uncached = [no for no in batch_nos if no not in cache.batch_cache]
    
    if uncached:
        # Single query for all uncached batches
        result = frappe.db.get_all(
            "Batch",
            filters={"name": ["in", uncached]},
            fields=["name", "disabled", "expiry_date"],
        )
        for row in result:
            cache.batch_cache[row['name']] = {
                'disabled': row.get('disabled', 0),
                'expiry_date': row.get('expiry_date'),
            }
    
    return {
        no: cache.batch_cache.get(no, {})
        for no in batch_nos
    }


def optimize_validate_item(stock_entry):
    """Optimized validate_item for bulk imports using batch queries."""
    if not should_optimize_import():
        return False  # Use original validation
    
    from erpnext.stock.get_item_details import get_item_group_defaults, get_brand_defaults, get_default_cost_center
    
    stock_items = stock_entry.get_stock_items()
    item_codes = list(set(item.item_code for item in stock_entry.get("items")))
    
    # Batch fetch all item details at once
    item_details_map = get_item_details_batch(item_codes, stock_entry.company)
    
    for item in stock_entry.get("items"):
        if not item.item_code in stock_items:
            frappe.throw(f"{item.item_code} is not a stock Item")
        
        if not item.item_code in item_details_map:
            frappe.throw(f"Item {item.item_code} is not active or end of life has been reached")
        
        item_details = item_details_map[item.item_code]
        if not item_details:
            frappe.throw(f"Item {item.item_code} is not active or end of life has been reached")
        
        # Set basic fields
        item.set("stock_uom", item_details.get("stock_uom"))
        item.set("item_name", item_details.get("item_name"))
        
        # Set optional fields if not already set
        if not item.get("uom"):
            item.set("uom", item_details.get("stock_uom"))
        if not item.get("description"):
            item.set("description", item_details.get("description"))
        if not item.get("conversion_factor"):
            item.set("conversion_factor", 1)
        
        # Set transfer_qty if needed
        if not item.transfer_qty and item.qty:
            item.transfer_qty = float(item.qty) * float(item.get("conversion_factor", 1))
    
    return True  # Validation complete


def optimize_validate_batch(stock_entry):
    """Optimized validate_batch for bulk imports using batch queries."""
    if not should_optimize_import():
        return False
    
    purposes_with_batch_check = [
        "Material Transfer for Manufacture",
        "Manufacture",
        "Repack",
        "Send to Subcontractor",
    ]
    
    if stock_entry.purpose not in purposes_with_batch_check:
        return True
    
    # Collect all batch numbers
    batch_nos = [item.batch_no for item in stock_entry.get("items") if item.batch_no]
    if not batch_nos:
        return True
    
    # Batch fetch all batch details at once
    batch_details_map = get_batch_details_batch(batch_nos)
    
    for item in stock_entry.get("items"):
        if not item.batch_no:
            continue
        
        batch_details = batch_details_map.get(item.batch_no, {})
        disabled = batch_details.get('disabled', 0)
        expiry_date = batch_details.get('expiry_date')
        
        if disabled == 0 and expiry_date:
            if getdate(stock_entry.posting_date) > getdate(expiry_date):
                frappe.throw(
                    f"Batch {item.batch_no} of Item {item.item_code} has expired."
                )
        elif disabled != 0:
            frappe.throw(
                f"Batch {item.batch_no} of Item {item.item_code} is disabled."
            )
    
    return True


def should_skip_valuation_rate_for_import(stock_entry):
    """For material transfers in import, use zero rate (no stock ledger scan)."""
    if not should_optimize_import():
        return False
    
    # Skip expensive valuation rate calculation for simple material transfers
    # during import. Rates can be recalculated post-import if needed.
    return stock_entry.purpose in [
        "Material Transfer",
        "Material Issue",
        "Material Receipt",
    ]


# Hook integration: Call before Stock Entry validate()
def optimize_stock_entry_before_validate(doc, method=None):
    """Pre-validation optimization hook."""
    if not should_optimize_import():
        return
    
    # Clear cache if this is the start of a new batch
    if not getattr(frappe.local, 'se_import_in_progress', False):
        frappe.local.se_import_in_progress = True
