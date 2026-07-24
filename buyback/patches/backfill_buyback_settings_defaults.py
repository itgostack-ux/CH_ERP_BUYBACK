"""Backfill defaults for reqd fields added to the Buyback Settings single.

Frappe applies DocField defaults only to NEW documents — an existing Singles
record never receives them, so the first save of Buyback Settings after
fields became mandatory dies with MandatoryError (observed blocking
`bench migrate` in the after_migrate ensure_default_settings hook).

Generic on purpose: reads the doctype meta and fills EVERY required field
that ships a default but has no stored value, so future reqd additions
cannot re-break migrate. Note ``frappe.db.set_single_value`` only UPDATEs
an existing tabSingles row — it silently no-ops when the row is missing,
hence the direct INSERT. Idempotent: only fills blanks.
"""

import frappe


def execute():
	frappe.reload_doc("buyback", "doctype", "buyback_settings")
	meta = frappe.get_meta("Buyback Settings")
	for df in meta.fields:
		if not df.reqd or df.default in (None, ""):
			continue
		row = frappe.db.sql(
			"SELECT value FROM tabSingles WHERE doctype='Buyback Settings' AND field=%s",
			(df.fieldname,),
		)
		if row and row[0][0] not in (None, ""):
			continue
		if row:
			frappe.db.sql(
				"UPDATE tabSingles SET value=%s WHERE doctype='Buyback Settings' AND field=%s",
				(df.default, df.fieldname),
			)
		else:
			frappe.db.sql(
				"INSERT INTO tabSingles (doctype, field, value) VALUES ('Buyback Settings', %s, %s)",
				(df.fieldname, df.default),
			)
	frappe.clear_cache(doctype="Buyback Settings")
