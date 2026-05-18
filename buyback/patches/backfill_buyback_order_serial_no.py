# Copyright (c) 2026, GoStack and contributors
# License: see license.txt

"""
Backfill `Buyback Order.serial_no` from the legacy `imei_serial` field.

Context
-------
As part of the additive rename `imei_serial → serial_no`, both fields exist
on the DocType and are kept in sync at validate-time via
`buyback_order._sync_serial_no_aliases`. This patch copies the historical
data once so that the new field is populated for every existing row.

Safety
------
- Idempotent: only updates rows where `serial_no` is NULL or empty.
- Read-only of `imei_serial` (the legacy field is preserved untouched).
- No-op on a fresh database.
"""

import frappe


def execute() -> None:
	# Skip cleanly if the new column hasn't been migrated yet (defensive — the
	# patch is registered post_model_sync so this should always pass).
	if not frappe.db.has_column("Buyback Order", "serial_no"):
		return

	updated = frappe.db.sql(
		"""
		UPDATE `tabBuyback Order`
		SET serial_no = imei_serial
		WHERE (serial_no IS NULL OR serial_no = '')
		  AND imei_serial IS NOT NULL
		  AND imei_serial != ''
		"""
	)
	# UPDATE returns rowcount via the cursor; surface a single info line so
	# the migration log shows the backfill volume.
	rowcount = frappe.db.sql("SELECT ROW_COUNT()")[0][0]
	frappe.logger().info(
		f"buyback.backfill_buyback_order_serial_no: copied {rowcount} row(s)"
	)
