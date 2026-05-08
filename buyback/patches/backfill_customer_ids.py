"""Backfill ch_customer_id and ch_membership_id on existing Buyback documents.

Runs once after the custom fields are added via migration.

Safety guards:
  - Skips entirely if ch_customer_id does not yet exist on the Customer table
    (means ch_item_master custom fields haven't been applied — safe to retry
    after running bench migrate again once they are).
  - Skips individual buyback tables that don't yet have the column.
  - Fully idempotent — only updates rows where the value is still missing.
"""

import frappe


def execute():
    # Guard: ch_customer_id is a custom field installed by ch_item_master.
    # If that app hasn't been migrated yet the column won't exist — skip and
    # let the next bench migrate pick this up.
    if not frappe.db.has_column("Customer", "ch_customer_id"):
        frappe.logger("patch").warning(
            "backfill_customer_ids: ch_customer_id column missing on Customer — "
            "skipping (re-run bench migrate after ch_item_master is migrated)"
        )
        return

    has_membership = frappe.db.has_column("Customer", "ch_membership_id")

    cust_rows = frappe.db.sql(
        "SELECT name, ch_customer_id"
        + (", ch_membership_id" if has_membership else "")
        + " FROM `tabCustomer`"
        " WHERE ch_customer_id IS NOT NULL AND ch_customer_id != 0",
        as_dict=True,
    )
    if not cust_rows:
        return

    cust_map = {r.name: r for r in cust_rows}

    doctypes = [
        ("Buyback Inspection", "customer"),
        ("Buyback Assessment", "customer"),
        ("Buyback Order", "customer"),
        ("Buyback Exchange Order", "customer"),
    ]

    for doctype, customer_col in doctypes:
        table = "tab" + doctype

        if not frappe.db.table_exists(table):
            continue
        if not frappe.db.has_column(doctype, "ch_customer_id"):
            frappe.logger("patch").warning(
                "backfill_customer_ids: ch_customer_id missing on %s — skipping", doctype
            )
            continue

        rows = frappe.db.sql(
            "SELECT name, `" + customer_col + "` AS customer"
            " FROM `" + table + "`"
            " WHERE `" + customer_col + "` IS NOT NULL"
            "   AND `" + customer_col + "` != ''"
            "   AND (ch_customer_id IS NULL OR ch_customer_id = 0)",
            as_dict=True,
        )

        updated = 0
        for row in rows:
            cust = cust_map.get(row.customer)
            if not cust:
                continue

            set_parts = ["ch_customer_id = %s"]
            params = [cust.ch_customer_id]

            if has_membership and frappe.db.has_column(doctype, "ch_membership_id"):
                set_parts.append("ch_membership_id = %s")
                params.append(cust.get("ch_membership_id") or "")

            params.append(row.name)
            frappe.db.sql(
                "UPDATE `" + table + "` SET " + ", ".join(set_parts) + " WHERE name = %s",
                params,
            )
            updated += 1

        if updated:
            frappe.logger("patch").info(
                "backfill_customer_ids: updated %d rows in %s", updated, doctype
            )

    frappe.db.commit()
