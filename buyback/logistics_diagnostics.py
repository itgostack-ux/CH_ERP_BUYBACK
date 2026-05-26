"""Logistics redesign — Phase 1 diagnostics.

Read-only utilities to inspect existing Buyback Orders for data quality issues
introduced by the legacy auto-pickup flow. Designed to be run via
``bench --site <site> execute`` and to print actionable results without
mutating any stock posting.

Examples:

    bench --site erpnext.local execute \
        buyback.logistics_diagnostics.report_buyback_se_not_in_bin

    bench --site erpnext.local execute \
        buyback.logistics_diagnostics.report_orphan_pickup_mrs
"""

from __future__ import annotations

import frappe


def report_buyback_se_not_in_bin(print_results: bool = True) -> list[dict]:
    """List Paid Buyback Orders whose Stock Entry target warehouse is NOT a
    Buyback Bin (``ch_bin_type='Buyback'``).

    These rows received the device into a generic store warehouse instead of
    the Buyback Bin and may need a manual reversal + reposting once the
    Buyback Bin is in place.
    """
    rows = frappe.db.sql(
        """
        SELECT
            bo.name AS buyback_order,
            bo.store,
            bo.stock_entry,
            sed.t_warehouse AS target_warehouse,
            w.ch_bin_type,
            bo.creation,
            bo.final_price
        FROM `tabBuyback Order` bo
        INNER JOIN `tabStock Entry` se ON se.name = bo.stock_entry AND se.docstatus = 1
        INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
        LEFT JOIN `tabWarehouse` w ON w.name = sed.t_warehouse
        WHERE bo.status = 'Paid'
          AND bo.stock_entry IS NOT NULL
          AND COALESCE(w.ch_bin_type, '') != 'Buyback'
        ORDER BY bo.creation DESC
        """,
        as_dict=True,
    )
    if print_results:
        print(f"Buyback Orders with SE not in Buyback Bin: {len(rows)}")
        for r in rows[:50]:
            print(
                f"  {r.buyback_order}  store={r.store}  se={r.stock_entry}  "
                f"target={r.target_warehouse}  bin_type={r.ch_bin_type or '<none>'}"
            )
        if len(rows) > 50:
            print(f"  ... and {len(rows) - 50} more")
    return rows


def report_orphan_pickup_mrs(print_results: bool = True) -> list[dict]:
    """List open Material Requests that were auto-created by the legacy
    pickup flow but are not yet fulfilled. Useful before flipping the flag
    so logistics can decide whether to close them out or keep them.
    """
    rows = frappe.db.sql(
        """
        SELECT
            mr.name,
            mr.custom_buyback_order AS buyback_order,
            mr.transaction_date,
            mr.status,
            mr.per_ordered,
            mri.from_warehouse,
            mri.warehouse AS to_warehouse,
            mri.item_code
        FROM `tabMaterial Request` mr
        INNER JOIN `tabMaterial Request Item` mri ON mri.parent = mr.name
        WHERE mr.docstatus = 1
          AND mr.material_request_type = 'Material Transfer'
          AND mr.custom_buyback_order IS NOT NULL
          AND mr.status IN ('Pending', 'Partially Ordered')
        ORDER BY mr.transaction_date DESC
        """,
        as_dict=True,
    )
    if print_results:
        print(f"Open auto-created pickup MRs: {len(rows)}")
        for r in rows[:50]:
            print(
                f"  {r.name}  bo={r.buyback_order}  {r.from_warehouse} -> "
                f"{r.to_warehouse}  status={r.status}  done%={r.per_ordered}"
            )
        if len(rows) > 50:
            print(f"  ... and {len(rows) - 50} more")
    return rows
