"""buyback bench-execute-callable recipes.

Recipes are importable functions that perform one end-to-end business
flow. Consumed by:

* E2E tests (``buyback/tests/e2e/``)
* ``bench execute`` for dev-loop / UAT smoke testing
* CI Step 5 workflows

Heavy scenarios are exercised by the discovered Buyback QA test suite.
"""
