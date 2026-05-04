"""Backfill ch_customer_id and ch_membership_id on existing Buyback documents.

Runs once after the custom fields are added via migration.
Fetches Customer master values in bulk and updates each affected doctype
with a single batch UPDATE per doctype.
"""

import frappe


def execute():
    doctypes = [
        ("tabBuyback Inspection", "customer"),
        ("tabBuyback Assessment", "customer"),
        ("tabBuyback Order", "customer"),
        ("tabBuyback Exchange Order", "customer"),
    ]

    # Build a map of customer → (ch_customer_id, ch_membership_id)
    cust_rows = frappe.db.sql(
        """
        SELECT name, ch_customer_id, ch_membership_id
        FROM `tabCustomer`
        WHERE ch_customer_id IS NOT NULL AND ch_customer_id != 0
        """,
        as_dict=True,
    )
    if not cust_rows:
        return

    cust_map = {r.name: r for r in cust_rows}

    for table, customer_col in doctypes:
        # Skip if the columns don't exist yet (guard against partial installs)
        try:
            frappe.db.sql(f"SELECT ch_customer_id FROM `{table}` LIMIT 1")
        except Exception:
            continue

        rows = frappe.db.sql(
            f"""
            SELECT name, {customer_col} AS customer
            FROM `{table}`
            WHERE ({customer_col} IS NOT NULL AND {customer_col} != '')
              AND (ch_customer_id IS NULL OR ch_customer_id = 0)
            """,
            as_dict=True,
        )

        for row in rows:
            cust = cust_map.get(row.customer)
            if not cust:
                continue
            frappe.db.sql(
                f"""
                UPDATE `{table}`
                SET ch_customer_id = %s, ch_membership_id = %s
                WHERE name = %s
                """,
                (cust.ch_customer_id, cust.ch_membership_id, row.name),
            )

    frappe.db.commit()
