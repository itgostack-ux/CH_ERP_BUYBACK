__version__ = "0.0.1"

# Apply Stock Entry bulk import optimization patch
try:
	from .stock_entry_import_patch import patch_stock_entry_validate
	patch_stock_entry_validate()
except Exception as e:
	# Silently fail if stock entry module not available
	pass
