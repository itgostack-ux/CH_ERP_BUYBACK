# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

def boot_session(bootinfo):
	"""Push Buyback settings to client at login."""
	import frappe
	if frappe.db.exists("DocType", "Buyback Settings"):
		settings = frappe.get_cached_doc("Buyback Settings")
		bootinfo["buyback_settings"] = {
			"enable_live_pricing": getattr(settings, "enable_live_pricing", 0),
			"default_settlement_type": getattr(settings, "default_settlement_type", "Cash"),
		}
