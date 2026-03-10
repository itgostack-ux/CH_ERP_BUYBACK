# Copyright (c) 2026, GoStack and contributors
# Performance Scorecards — Store, Inspector, Executive

"""
Composite scoring system (0–100):
  - Each dimension is normalised to 0-100
  - Final score = weighted average of dimensions
  - Weights configurable but default as below

Store Scorecard (30-30-20-20):
  30%  Quote→Order conversion rate
  30%  SLA compliance (order creation → approval within target)
  20%  Avg order value (normalised against branch average)
  20%  Inverse rejection rate (100 - rejection_pct)

Inspector Scorecard (25-25-25-25):
  25%  Inspections completed per day
  25%  Avg inspection duration (shorter = better, capped at target)
  25%  Grade A accuracy (A-grade % vs benchmark)
  25%  Linked order conversion (inspections that led to paid orders)

Executive Scorecard (30-30-20-20):
  30%  Orders handled
  30%  Conversion rate (quotes → paid orders)
  20%  SLA compliance
  20%  Customer satisfaction proxy (low rejection/cancellation)
"""

import frappe
from frappe import _
from frappe.utils import (
    nowdate, add_days, add_months, flt, cint,
    get_datetime, time_diff_in_seconds,
)
from buyback.buyback.sla_engine import calculate_sla_status, DEFAULT_SLAS


# ═══════════════════════════════════════════════════════════════════
# STORE SCORECARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_store_scorecards(from_date=None, to_date=None, company=None):
    """Compute scorecard for all active stores."""
    from_date = from_date or add_months(nowdate(), -1)
    to_date = to_date or nowdate()
    company_cond = f"AND company = {frappe.db.escape(company)}" if company else ""

    stores = frappe.db.sql(f"""
        SELECT store,
            COUNT(*) as total_orders,
            SUM(CASE WHEN status IN ('Paid','Closed') THEN 1 ELSE 0 END) as paid_orders,
            COALESCE(SUM(CASE WHEN status IN ('Paid','Closed') THEN total_paid ELSE 0 END), 0) as total_payout,
            SUM(CASE WHEN status IN ('Rejected','Cancelled') THEN 1 ELSE 0 END) as rejected
        FROM `tabBuyback Order`
        WHERE docstatus < 2
            AND creation BETWEEN '{from_date}' AND '{to_date} 23:59:59'
            {company_cond}
        GROUP BY store
    """, as_dict=1)

    if not stores:
        return []

    # Global benchmarks
    global_avg_order = sum(s.total_payout for s in stores) / max(sum(s.paid_orders for s in stores), 1)

    results = []
    for s in stores:
        # Quotes for this store
        assessments = frappe.db.count("Buyback Assessment", {
            "store": s.store,
            "creation": ("between", [from_date, to_date + " 23:59:59"]),
        }) or 1

        conversion_rate = min(s.total_orders / assessments * 100, 100)
        avg_order = s.total_payout / max(s.paid_orders, 1)
        rejection_pct = s.rejected / max(s.total_orders, 1) * 100

        # SLA compliance
        sla_orders = frappe.db.sql(f"""
            SELECT creation, approval_date
            FROM `tabBuyback Order`
            WHERE docstatus < 2 AND store = {frappe.db.escape(s.store)}
                AND creation BETWEEN '{from_date}' AND '{to_date} 23:59:59'
        """, as_dict=1)

        sla_ok = sum(
            1 for o in sla_orders
            if calculate_sla_status(o.creation, o.approval_date, DEFAULT_SLAS["confirmation_to_approval"])["status"] != "Breach"
        )
        sla_compliance = sla_ok / max(len(sla_orders), 1) * 100

        # Normalised scores (0-100)
        s_conversion = min(conversion_rate / 80 * 100, 100)  # 80% conversion = perfect
        s_sla = min(sla_compliance, 100)
        s_avg_value = min(avg_order / max(global_avg_order, 1) * 100, 100) if global_avg_order else 50
        s_rejection = max(100 - rejection_pct * 3, 0)  # 33% rejection = 0

        composite = round(
            s_conversion * 0.30 +
            s_sla * 0.30 +
            s_avg_value * 0.20 +
            s_rejection * 0.20,
            1
        )

        results.append({
            "store": s.store,
            "total_orders": s.total_orders,
            "paid_orders": s.paid_orders,
            "total_payout": s.total_payout,
            "conversion_rate": round(conversion_rate, 1),
            "sla_compliance": round(sla_compliance, 1),
            "avg_order_value": round(avg_order, 2),
            "rejection_pct": round(rejection_pct, 1),
            "composite_score": composite,
            "grade": _score_grade(composite),
        })

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    return results


# ═══════════════════════════════════════════════════════════════════
# INSPECTOR SCORECARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_inspector_scorecards(from_date=None, to_date=None, store=None):
    """Compute scorecard for all inspectors."""
    from_date = from_date or add_months(nowdate(), -1)
    to_date = to_date or nowdate()
    store_cond = f"AND store = {frappe.db.escape(store)}" if store else ""

    inspectors = frappe.db.sql(f"""
        SELECT
            inspector,
            COUNT(*) as total_inspections,
            SUM(CASE WHEN status = 'Completed' THEN 1 ELSE 0 END) as completed,
            AVG(
                CASE WHEN inspection_completed_at IS NOT NULL AND inspection_started_at IS NOT NULL
                THEN TIMESTAMPDIFF(MINUTE, inspection_started_at, inspection_completed_at)
                END
            ) as avg_duration_min,
            SUM(CASE WHEN condition_grade = 'A' OR condition_grade LIKE 'A%%' THEN 1 ELSE 0 END) as grade_a_count,
            DATEDIFF('{to_date}', '{from_date}') + 1 as days_in_range
        FROM `tabBuyback Inspection`
        WHERE creation BETWEEN '{from_date}' AND '{to_date} 23:59:59'
            {store_cond}
        GROUP BY inspector
    """, as_dict=1)

    results = []
    for insp in inspectors:
        if not insp.inspector:
            continue

        items_per_day = insp.completed / max(insp.days_in_range or 1, 1)
        grade_a_pct = insp.grade_a_count / max(insp.total_inspections, 1) * 100

        # Linked order conversion — how many inspections led to paid orders
        linked_paid = frappe.db.sql(f"""
            SELECT COUNT(*) as cnt
            FROM `tabBuyback Order`
            WHERE buyback_inspection IN (
                SELECT name FROM `tabBuyback Inspection`
                WHERE inspector = {frappe.db.escape(insp.inspector)}
                    AND creation BETWEEN '{from_date}' AND '{to_date} 23:59:59'
            )
            AND status IN ('Paid', 'Closed')
        """, as_dict=1)[0].cnt

        order_conversion = linked_paid / max(insp.completed, 1) * 100

        # Scoring
        s_throughput = min(items_per_day / 10 * 100, 100)  # 10/day = perfect
        s_speed = min(max(30 - (insp.avg_duration_min or 30), 0) / 30 * 100 + 50, 100)  # faster = better
        s_grade_a = min(grade_a_pct / 40 * 100, 100)  # 40% A-grade = perfect
        s_conversion = min(order_conversion / 70 * 100, 100)  # 70% conversion = perfect

        composite = round(
            s_throughput * 0.25 +
            s_speed * 0.25 +
            s_grade_a * 0.25 +
            s_conversion * 0.25,
            1
        )

        results.append({
            "inspector": insp.inspector,
            "total_inspections": insp.total_inspections,
            "completed": insp.completed,
            "avg_duration_min": round(insp.avg_duration_min or 0, 1),
            "items_per_day": round(items_per_day, 1),
            "grade_a_pct": round(grade_a_pct, 1),
            "order_conversion_pct": round(order_conversion, 1),
            "composite_score": composite,
            "grade": _score_grade(composite),
        })

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    return results


# ═══════════════════════════════════════════════════════════════════
# EXECUTIVE SCORECARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_executive_scorecards(from_date=None, to_date=None, store=None):
    """Compute scorecard for buyback agents/executives."""
    from_date = from_date or add_months(nowdate(), -1)
    to_date = to_date or nowdate()
    store_cond = f"AND store = {frappe.db.escape(store)}" if store else ""

    executives = frappe.db.sql(f"""
        SELECT
            owner as executive,
            COUNT(*) as total_orders,
            SUM(CASE WHEN status IN ('Paid','Closed') THEN 1 ELSE 0 END) as paid_orders,
            COALESCE(SUM(CASE WHEN status IN ('Paid','Closed') THEN total_paid ELSE 0 END), 0) as total_payout,
            SUM(CASE WHEN status IN ('Rejected','Cancelled') THEN 1 ELSE 0 END) as rejected
        FROM `tabBuyback Order`
        WHERE docstatus < 2
            AND creation BETWEEN '{from_date}' AND '{to_date} 23:59:59'
            {store_cond}
        GROUP BY owner
    """, as_dict=1)

    results = []
    for ex in executives:
        if not ex.executive or ex.executive == "Administrator":
            continue

        # Quotes by this executive
        assessments = frappe.db.count("Buyback Assessment", {
            "owner": ex.executive,
            "creation": ("between", [from_date, to_date + " 23:59:59"]),
        }) or 1

        conversion = min(ex.paid_orders / assessments * 100, 100)
        rejection_pct = ex.rejected / max(ex.total_orders, 1) * 100

        # SLA compliance
        sla_orders = frappe.db.sql(f"""
            SELECT creation, approval_date
            FROM `tabBuyback Order`
            WHERE docstatus < 2 AND owner = {frappe.db.escape(ex.executive)}
                AND creation BETWEEN '{from_date}' AND '{to_date} 23:59:59'
        """, as_dict=1)

        sla_ok = sum(
            1 for o in sla_orders
            if calculate_sla_status(o.creation, o.approval_date, DEFAULT_SLAS["confirmation_to_approval"])["status"] != "Breach"
        )
        sla_compliance = sla_ok / max(len(sla_orders), 1) * 100

        # Scoring
        s_orders = min(ex.total_orders / 50 * 100, 100)  # 50 orders in period = perfect
        s_conversion = min(conversion / 70 * 100, 100)
        s_sla = min(sla_compliance, 100)
        s_satisfaction = max(100 - rejection_pct * 3, 0)

        composite = round(
            s_orders * 0.30 +
            s_conversion * 0.30 +
            s_sla * 0.20 +
            s_satisfaction * 0.20,
            1
        )

        results.append({
            "executive": ex.executive,
            "total_orders": ex.total_orders,
            "paid_orders": ex.paid_orders,
            "total_payout": ex.total_payout,
            "conversion_pct": round(conversion, 1),
            "sla_compliance": round(sla_compliance, 1),
            "rejection_pct": round(rejection_pct, 1),
            "composite_score": composite,
            "grade": _score_grade(composite),
        })

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    return results


# ─── Helpers ─────────────────────────────────────────────────────────

def _score_grade(score):
    """Convert numeric score to letter grade."""
    if score >= 90:
        return "A+"
    elif score >= 80:
        return "A"
    elif score >= 70:
        return "B"
    elif score >= 60:
        return "C"
    elif score >= 50:
        return "D"
    else:
        return "F"
