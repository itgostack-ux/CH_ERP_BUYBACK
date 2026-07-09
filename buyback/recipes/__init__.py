"""buyback bench-execute-callable recipes.

Recipes are importable functions that perform one end-to-end business
flow. Consumed by:

* E2E tests (``buyback/tests/e2e/``)
* ``bench execute`` for dev-loop / UAT smoke testing
* CI Step 5 workflows

For heavy-scenario recipes see ``buyback/qa/runner.py`` and
``buyback/qa/scenarios.py`` (the pre-existing 27-scenario QA harness).
"""
