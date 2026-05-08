"""Force-sync utility for teammates whose patch log already has entries.

If bench migrate ran before the fixes were applied, the patches get logged
as done even though nothing was actually updated. Use this script to
re-run the logic regardless of patch log state.

Run from the bench directory:
    bench --site <site-name> execute buyback.buyback.patches.force_sync_customer_ids.run

Or from bench console:
    from buyback.buyback.patches import force_sync_customer_ids
    force_sync_customer_ids.run()
"""

import frappe
from buyback.buyback.patches import backfill_customer_ids, seed_question_categories


def run():
    print("=== Buyback force-sync ===")

    print("\n[1/2] Seeding Question Categories...")
    seed_question_categories.execute()
    print("      done.")

    print("\n[2/2] Backfilling customer IDs on Buyback documents...")
    backfill_customer_ids.execute()
    print("      done.")

    frappe.db.commit()
    print("\n✓ Force-sync complete. Run `bench clear-cache` to flush UI cache.")
