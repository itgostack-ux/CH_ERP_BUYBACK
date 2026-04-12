import frappe


def execute():
    """Populate sku_id from buyback_price_id before the unique constraint is added.

    Without this, adding sku_id (Int, unique) via schema sync would give all
    existing rows the default value 0, causing a duplicate-key error.
    """
    table = "tabBuyback Price Master"

    # Check if the column already exists
    columns = frappe.db.sql(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = 'sku_id'",
        (table,),
    )
    if columns:
        # Column exists — just backfill any rows still at 0
        frappe.db.sql(f"""
            UPDATE `{table}`
            SET sku_id = buyback_price_id
            WHERE sku_id = 0 OR sku_id IS NULL
        """)
    else:
        # Column doesn't exist yet — add it and populate before Frappe creates the unique index
        frappe.db.sql_ddl(f"ALTER TABLE `{table}` ADD COLUMN `sku_id` int(11) NOT NULL DEFAULT 0")
        frappe.db.sql(f"UPDATE `{table}` SET sku_id = buyback_price_id")

    frappe.db.commit()
