"""
Buyback QA Module
==================
Top-level convenience API for the QA automation system.

Quick usage::

    bench --site erpnext.local execute buyback.qa.run_all
    bench --site erpnext.local execute buyback.qa.run_scenario --kwargs '{"scenario_id":"S01"}'
    bench --site erpnext.local execute buyback.qa.seed
    bench --site erpnext.local execute buyback.qa.cleanup
"""

from buyback.qa.runner import run_all, run_scenario
from buyback.qa.factory import seed_all as seed, cleanup_all as cleanup

__all__ = ["run_all", "run_scenario", "seed", "cleanup"]
