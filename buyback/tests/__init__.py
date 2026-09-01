# Copyright (c) 2026, Congruence Holdings and contributors
# See license.txt

"""Shared helpers for the buyback test suites."""

import frappe


def test_item_compliance_fields() -> dict:
	"""Fields other installed apps make mandatory on every Item.

	Test items used to be created bare, which worked only while a copy
	survived in site data; on a wiped/fresh site india_compliance rejects
	an Item without a 6/8-digit HSN and ch_item_master rejects one without
	the CH category hierarchy, so every suite died in setUpClass before
	reaching a single assertion. Central so the suites cannot drift apart.
	"""
	sub = frappe.db.get_value(
		"CH Sub Category", {}, ["name", "category"], as_dict=True
	) or frappe._dict()
	return {
		# 851711 is the telephone-set chapter the test handsets fall under.
		"gst_hsn_code": frappe.db.get_value("GST HSN Code", "851711", "name")
			or frappe.db.get_value("GST HSN Code", {"name": ("like", "8517__")}, "name"),
		"ch_category": sub.get("category"),
		"ch_sub_category": sub.get("name"),
	}
