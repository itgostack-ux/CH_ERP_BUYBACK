import frappe


def execute():
    """Populate sku_id from buyback_price_id before the unique constraint is added.

    Without this, adding sku_id (Int, unique) via schema sync would give all
    existing rows the default value 0, causing a duplicate-key error.
    """
    columns = frappe.db.sql(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = 'sku_id'",
        ("tabBuyback Price Master",),
    )
    if columns:
        frappe.db.sql(
            "UPDATE `tabBuyback Price Master` SET sku_id = buyback_price_id WHERE sku_id = 0 OR sku_id IS NULL"
        )
    else:
        frappe.db.sql_ddl(
            "ALTER TABLE `tabBuyback Price Master` ADD COLUMN `sku_id` int(11) NOT NULL DEFAULT 0"
        )
        frappe.db.sql("UPDATE `tabBuyback Price Master` SET sku_id = buyback_price_id")

    frappe.db.commit()
