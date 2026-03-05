"""
Buyback QA – Runner
====================
Orchestrates seed → scenario execution → result recording.

Usage from bench console::

    import frappe
    frappe.init(site="erpnext.local")
    frappe.connect()
    from buyback.qa.runner import run_all, run_scenario
    run_all()                # runs all 17 scenarios
    run_scenario("S01")      # runs one specific scenario

Or via bench command::

    bench --site erpnext.local execute buyback.qa.run_all
    bench --site erpnext.local execute buyback.qa.run_scenario --kwargs '{"scenario_id":"S01"}'
"""

from __future__ import annotations

import time
import traceback
import uuid

import frappe
from frappe.utils import now_datetime

from buyback.qa.factory import seed_all, cleanup_all
from buyback.qa.scenarios import get_all_scenarios, get_scenario


def run_all(
    company: str | None = None,
    cleanup_before: bool = False,
    seed: bool = True,
    scenario_ids: list[str] | None = None,
) -> dict:
    """
    Run all (or selected) QA scenarios.

    Args:
        company: Override company (default from factory.COMPANY)
        cleanup_before: If True, purge all QA data before seeding
        seed: If True, seed master data (idempotent)
        scenario_ids: Run only these scenario IDs (e.g. ["S01","S03"])

    Returns:
        dict with run_id, summary counts, and per-scenario results
    """
    run_id = f"QA-{uuid.uuid4().hex[:8].upper()}"
    frappe.flags.qa_run_id = run_id
    results: list[dict] = []

    try:
        if cleanup_before:
            frappe.logger("buyback.qa").info(f"[{run_id}] Cleaning up previous QA data...")
            cleanup_all()

        if seed:
            frappe.logger("buyback.qa").info(f"[{run_id}] Seeding master data...")
            summary = seed_all(company)
            frappe.logger("buyback.qa").info(f"[{run_id}] Seed summary: {summary}")

        scenarios = get_all_scenarios()
        if scenario_ids:
            scenarios = [s for s in scenarios if s["id"] in scenario_ids]

        for scenario in scenarios:
            result = _execute_scenario(run_id, scenario)
            results.append(result)

    except Exception as e:
        frappe.log_error(title=f"QA Runner Error [{run_id}]")
        results.append({
            "scenario_id": "RUNNER",
            "scenario_name": "Runner Infrastructure",
            "result": "Error",
            "error_message": str(e),
            "error_traceback": traceback.format_exc(),
            "execution_time": 0,
            "docs": [],
        })
    finally:
        frappe.flags.qa_run_id = None

    # Summary
    passed = sum(1 for r in results if r["result"] == "Pass")
    failed = sum(1 for r in results if r["result"] == "Fail")
    errors = sum(1 for r in results if r["result"] == "Error")
    total = len(results)

    report = {
        "run_id": run_id,
        "timestamp": str(now_datetime()),
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "summary": f"Pass: {passed}, Fail: {failed}, Error: {errors}, Total: {total}",
        "results": results,
    }

    _print_report(report)
    return report


def run_scenario(
    scenario_id: str,
    seed: bool = True,
    company: str | None = None,
) -> dict:
    """Run a single scenario by ID."""
    run_id = f"QA-{uuid.uuid4().hex[:8].upper()}"
    frappe.flags.qa_run_id = run_id

    if seed:
        seed_all(company)

    scenario = get_scenario(scenario_id)
    if not scenario:
        return {"error": f"Scenario {scenario_id} not found"}

    result = _execute_scenario(run_id, scenario)
    _print_report({
        "run_id": run_id,
        "timestamp": str(now_datetime()),
        "total": 1,
        "passed": 1 if result["result"] == "Pass" else 0,
        "failed": 1 if result["result"] == "Fail" else 0,
        "errors": 1 if result["result"] == "Error" else 0,
        "results": [result],
    })
    return result


def _extract_error_message(exc: Exception) -> str:
    """Extract error message from exception, falling back to frappe.message_log."""
    msg = str(exc).strip()
    if msg:
        return msg
    # frappe.throw() stores message in message_log, not in exception args
    if frappe.message_log:
        import json
        try:
            last = frappe.message_log[-1]
            if isinstance(last, dict):
                return last.get("message", "")
            return str(last)
        except Exception:
            pass
    return repr(exc)


def _execute_scenario(run_id: str, scenario: dict) -> dict:
    """Execute a single scenario with error handling and result recording."""
    sid = scenario["id"]
    name = scenario["name"]
    fn = scenario["fn"]
    ctx: dict = {"docs": []}

    frappe.logger("buyback.qa").info(f"[{run_id}] Running {sid}: {name}")

    start = time.time()
    result_status = "Error"
    message = ""
    tb = ""

    try:
        frappe.message_log = []  # clear before each scenario
        passed, message = fn(ctx)
        result_status = "Pass" if passed else "Fail"
        frappe.db.commit()  # persist created docs for subsequent scenarios
    except AssertionError as e:
        result_status = "Fail"
        message = _extract_error_message(e)
        tb = traceback.format_exc()
        try:
            frappe.db.rollback()
        except Exception:
            pass
        frappe.logger("buyback.qa").warning(f"[{run_id}] {sid} FAILED: {message}")
    except Exception as e:
        result_status = "Error"
        message = _extract_error_message(e)
        tb = traceback.format_exc()
        try:
            frappe.db.rollback()
        except Exception:
            pass
        frappe.logger("buyback.qa").error(f"[{run_id}] {sid} ERROR: {message}\n{tb}")

    elapsed = round(time.time() - start, 3)

    record = {
        "scenario_id": sid,
        "scenario_name": name,
        "result": result_status,
        "error_message": message if result_status != "Pass" else "",
        "error_traceback": tb,
        "execution_time": elapsed,
        "docs": ctx.get("docs", []),
    }

    # Persist to Buyback QA Test Run DocType
    _save_test_run(run_id, record)
    return record


def _save_test_run(run_id: str, record: dict):
    """Save scenario result as a Buyback QA Test Run document."""
    try:
        doc = frappe.get_doc({
            "doctype": "Buyback QA Test Run",
            "run_id": run_id,
            "scenario_id": record["scenario_id"],
            "scenario_name": record["scenario_name"],
            "status": "Completed",
            "result": record["result"],
            "execution_time": record["execution_time"],
            "error_message": record.get("error_message", ""),
            "error_traceback": record.get("error_traceback", ""),
            "executed_by": frappe.session.user,
            "run_timestamp": now_datetime(),
            "created_docs": [
                {
                    "doctype_name": d["doctype"],
                    "doc_name": d["name"],
                    "description": d.get("description", ""),
                }
                for d in record.get("docs", [])
            ],
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.log_error(title=f"Failed to save QA test run {run_id}/{record['scenario_id']}")


def _print_report(report: dict):
    """Pretty-print the test report to console."""
    sep = "=" * 72
    print(f"\n{sep}")
    print(f"  BUYBACK QA TEST REPORT")
    print(f"  Run ID:    {report['run_id']}")
    print(f"  Timestamp: {report['timestamp']}")
    print(f"{sep}")
    print(f"  Total: {report['total']}  |  Pass: {report['passed']}  |  "
          f"Fail: {report['failed']}  |  Error: {report['errors']}")
    print(sep)

    for r in report["results"]:
        icon = {"Pass": "✅", "Fail": "❌", "Error": "💥"}.get(r["result"], "❓")
        line = f"  {icon} {r['scenario_id']:6s} {r['scenario_name']:<45s} " \
               f"{r['execution_time']:>6.3f}s"
        print(line)
        if r.get("error_message") and r["result"] != "Pass":
            print(f"         ↳ {r['error_message'][:100]}")
        if r.get("docs"):
            for d in r["docs"][:3]:
                print(f"         📄 {d['doctype']}: {d['name']}")
            if len(r["docs"]) > 3:
                print(f"         ... +{len(r['docs'])-3} more docs")

    print(sep)
    if report["failed"] == 0 and report["errors"] == 0:
        print("  🎉 ALL SCENARIOS PASSED!")
    else:
        print(f"  ⚠️  {report['failed'] + report['errors']} scenario(s) need attention")
    print(f"{sep}\n")
