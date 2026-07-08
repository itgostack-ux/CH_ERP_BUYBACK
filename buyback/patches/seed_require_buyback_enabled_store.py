"""Phase A — Buyback market-standard hardening.

Seed the new ``Buyback Settings.require_buyback_enabled_store`` flag so the
store-eligibility gate is ON by default for all existing sites (Frappe does
not backfill JSON defaults on Single doctypes when a new field is added).

Market context (Cashify, Samsung Exchange, Best Buy Trade-In, Apple Trade In):
buyback intake is only allowed at pre-approved / authorized centres. This gate
enforces that at the ``buyback.api.create_order`` boundary.

Admins can still turn it OFF from Buyback Settings if a pilot rollout needs to
bypass the check.

Safe to re-run.
"""

import frappe
from frappe.utils import cint


def execute():
    if not frappe.db.exists("DocType", "Buyback Settings"):
        return

    meta = frappe.get_meta("Buyback Settings")
    if not meta.get_field("require_buyback_enabled_store"):
        # Custom field hasn't landed yet on this site.
        return

    current = frappe.db.get_single_value(
        "Buyback Settings", "require_buyback_enabled_store"
    )
    if cint(current) == 1:
        # Already ON — nothing to do.
        return

    # One-time seed: patches run once per site by design, so setting the value
    # here does not fight future admin overrides — this only ever fires the
    # first time this Phase A patch lands on the site.
    frappe.db.set_single_value(
        "Buyback Settings", "require_buyback_enabled_store", 1
    )
    frappe.logger("buyback").info(
        "[phase-a] Seeded Buyback Settings.require_buyback_enabled_store = 1 "
        "(market-standard default; was %s)." % current
    )
