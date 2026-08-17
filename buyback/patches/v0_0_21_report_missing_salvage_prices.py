"""Report Buyback Price Master rows with no Scrap or Phone Dead price.

The pricing engine falls back to the Scrap price when deductions take a quote
below the band's lowest grade, and returns the Phone Dead price outright for a
handset that does not power on. Both were ₹0 on every row, so the two salvage
paths quietly offered the customer nothing.

The engine now refuses to quote in that situation rather than offering ₹0, so
this patch does not need to fix the payout — it exists to name the rows a
pricing owner has to fill before those devices can be quoted at all.

It deliberately does NOT write prices. Buyback prices are maker/checker
controlled: every price field on this doctype is blocked from direct edit and
must flow through the CH Ready Reckoner batch approval. Inventing salvage
values in a migration would bypass exactly the control that exists to stop
that.
"""

import frappe


def execute():
    if not frappe.db.table_exists("Buyback Price Master"):
        return
    for column in ("scrap_price", "phone_dead_price"):
        if not frappe.db.has_column("Buyback Price Master", column):
            return

    gaps = frappe.db.sql(
        """
        SELECT name, item_code, item_name,
               scrap_price, phone_dead_price
        FROM `tabBuyback Price Master`
        WHERE IFNULL(scrap_price, 0) = 0 OR IFNULL(phone_dead_price, 0) = 0
        ORDER BY item_code
        """,
        as_dict=True,
    )
    if not gaps:
        print("✔ Every Buyback Price Master row has Scrap and Phone Dead prices")
        return

    total = frappe.db.count("Buyback Price Master")
    print(
        f"⚠ {len(gaps)} of {total} Buyback Price Master rows are missing a salvage "
        f"price. Devices that fall to scrap, and any handset marked Phone Dead, "
        f"cannot be quoted until these are set via the CH Ready Reckoner:"
    )
    for row in gaps[:25]:
        missing = []
        if not row.scrap_price:
            missing.append("Scrap")
        if not row.phone_dead_price:
            missing.append("Phone Dead")
        print(f"    {row.item_code} ({row.name}) — missing {' + '.join(missing)}")
    if len(gaps) > 25:
        print(f"    … and {len(gaps) - 25} more")
