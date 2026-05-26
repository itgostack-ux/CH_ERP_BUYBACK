"""Logistics redesign — Phase 1.

Back-correct existing data so the live system stops auto-creating Material
Transfer Requests when a Buyback Order is marked Paid. Store staff will raise
the pickup request manually from the new "Create Pickup Transfer Request"
button on the Buyback Order.

Steps:
1. Flip ``Buyback Settings.auto_create_pickup_request`` to 0 on the live row.
2. Ensure every active CH Store has a Buyback Bin sub-warehouse so the manual
   action has a valid source.

Safe to re-run.
"""

import frappe


def execute():
    _disable_setting()
    _ensure_store_bins()


def _disable_setting():
    if not frappe.db.exists("DocType", "Buyback Settings"):
        return
    current = frappe.db.get_single_value("Buyback Settings", "auto_create_pickup_request")
    if frappe.utils.cint(current) == 0:
        return
    frappe.db.set_single_value("Buyback Settings", "auto_create_pickup_request", 0)
    frappe.logger("buyback").info(
        "[logistics-phase1] Disabled Buyback Settings.auto_create_pickup_request "
        "(was %s). Pickup MRs are now manual." % current
    )


def _ensure_store_bins():
    """Idempotently materialise the Buyback Bin for every active CH Store."""
    if not frappe.db.exists("DocType", "CH Store"):
        return
    try:
        from ch_item_master.ch_core.doctype.ch_store.ch_store import ensure_store_bins
    except Exception:
        frappe.logger("buyback").warning(
            "[logistics-phase1] ensure_store_bins helper not importable; skipping."
        )
        return

    stores = frappe.get_all("CH Store", filters={"disabled": 0}, pluck="name")
    fixed = 0
    for name in stores:
        try:
            store = frappe.get_doc("CH Store", name)
            ensure_store_bins(store)
            fixed += 1
        except Exception:
            frappe.log_error(
                title="logistics-phase1: ensure_store_bins failed for %s" % name,
            )
    frappe.logger("buyback").info(
        "[logistics-phase1] ensure_store_bins ran for %d active store(s)." % fixed
    )
