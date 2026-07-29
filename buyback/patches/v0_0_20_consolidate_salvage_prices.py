# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""Collapse the per-warranty-band scrap / phone-dead prices into one field each.

Buyback Price Master used to carry eight salvage columns —
``scrap_iw_0_3 / _0_6 / _6_11 / _oow_11`` and the matching ``phone_dead_*`` —
and the pricing engine picked one by warranty status and device age. That split
has no business meaning: a handset that does not power on, or that grades out as
scrap, is worth its salvage value whatever its age and whether or not warranty
has lapsed. It only gave price managers eight cells to keep in sync and eight
chances to disagree.

The grade prices stay per band, and so does the THRESHOLD that triggers the
scrap floor (``_get_min_grade_price`` — C grade under 3 months, D grade after).
Only the salvage values themselves are flat now.

Carries the highest legacy value into the new field before the old columns go,
so a site that had populated them keeps the most generous salvage price rather
than silently dropping to zero.
"""

import frappe

_LEGACY_SCRAP = ("scrap_iw_0_3", "scrap_iw_0_6", "scrap_iw_6_11", "scrap_oow_11")
_LEGACY_DEAD = (
	"phone_dead_iw_0_3",
	"phone_dead_iw_0_6",
	"phone_dead_iw_6_11",
	"phone_dead_oow_11",
)


def execute():
	if not frappe.db.exists("DocType", "Buyback Price Master"):
		return

	frappe.reload_doc("buyback", "doctype", "buyback_price_master")

	table = "tabBuyback Price Master"
	present_scrap = [f for f in _LEGACY_SCRAP if frappe.db.has_column(table, f)]
	present_dead = [f for f in _LEGACY_DEAD if frappe.db.has_column(table, f)]
	if not present_scrap and not present_dead:
		return

	# Migrate before dropping: take the max across the old bands so a populated
	# site keeps a sensible salvage figure instead of losing it.
	for target, sources in (("scrap_price", present_scrap), ("phone_dead_price", present_dead)):
		if not sources or not frappe.db.has_column(table, target):
			continue
		greatest = ", ".join(f"IFNULL(`{c}`, 0)" for c in sources)
		frappe.db.sql(
			f"""
			UPDATE `{table}`
			   SET `{target}` = GREATEST({greatest})
			 WHERE IFNULL(`{target}`, 0) = 0
			   AND GREATEST({greatest}) > 0
			"""
		)

	migrated = frappe.db.sql(
		f"SELECT COUNT(*) FROM `{table}` WHERE IFNULL(`scrap_price`, 0) > 0 "
		f"OR IFNULL(`phone_dead_price`, 0) > 0"
	)[0][0]

	for column in present_scrap + present_dead:
		frappe.db.sql_ddl(f"ALTER TABLE `{table}` DROP COLUMN `{column}`")

	# Property Setters / Custom Fields pointing at the dropped columns would
	# resurrect them as phantom fields on the next meta build.
	for column in present_scrap + present_dead:
		frappe.db.delete("Custom Field", {"dt": "Buyback Price Master", "fieldname": column})
		frappe.db.delete("Property Setter", {"doc_type": "Buyback Price Master", "field_name": column})

	frappe.clear_cache(doctype="Buyback Price Master")
	frappe.db.commit()
	print(
		f"v0_0_20: dropped {len(present_scrap) + len(present_dead)} per-band salvage columns; "
		f"{migrated} row(s) carry a consolidated salvage price"
	)
