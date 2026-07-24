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
    add_months, date_diff, flt, getdate, nowdate,
)
from buyback.buyback.sla_engine import DEFAULT_SLAS
from buyback.utils import (
    assert_buyback_scope,
    build_buyback_scope_sql,
    get_buyback_setting_value,
    get_int_setting,
    require_configured_role,
)


_SCORECARD_DEFAULTS = {
    "store_scorecard_configuration": {
        "conversion_target_pct": 80,
        "rejection_zero_pct": 33.333,
        "conversion_weight": 30,
        "sla_weight": 30,
        "average_value_weight": 20,
        "rejection_weight": 20,
    },
    "inspector_scorecard_configuration": {
        "daily_target": 10,
        "duration_target_minutes": 30,
        "grade_a_target_pct": 40,
        "conversion_target_pct": 70,
        "throughput_weight": 25,
        "speed_weight": 25,
        "grade_a_weight": 25,
        "conversion_weight": 25,
    },
    "executive_scorecard_configuration": {
        "order_target": 50,
        "conversion_target_pct": 70,
        "rejection_zero_pct": 33.333,
        "orders_weight": 30,
        "conversion_weight": 30,
        "sla_weight": 20,
        "satisfaction_weight": 20,
    },
}
_GRADE_DEFAULTS = {"a_plus": 90, "a": 80, "b": 70, "c": 60, "d": 50}


def _scorecard_config(fieldname):
    defaults = _SCORECARD_DEFAULTS[fieldname]
    raw = get_buyback_setting_value(fieldname)
    try:
        configured = frappe.parse_json(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        configured = None
    if not isinstance(configured, dict):
        return frappe._dict(defaults)
    values = {}
    for key, default in defaults.items():
        value = flt(configured.get(key, default))
        values[key] = value if value > 0 else default
    return frappe._dict(values)


def _weighted_score(values, weights):
    total_weight = sum(max(flt(weight), 0) for weight in weights)
    if total_weight <= 0:
        return 0
    return round(
        sum(flt(value) * max(flt(weight), 0) for value, weight in zip(values, weights))
        / total_weight,
        1,
    )


def _grade_thresholds():
    raw = get_buyback_setting_value("scorecard_grade_thresholds")
    try:
        configured = frappe.parse_json(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        configured = None
    values = {
        key: flt(configured.get(key, default)) if isinstance(configured, dict) else default
        for key, default in _GRADE_DEFAULTS.items()
    }
    ordered = [values[key] for key in ("a_plus", "a", "b", "c", "d")]
    if not all(0 <= value <= 100 for value in ordered) or ordered != sorted(ordered, reverse=True):
        return frappe._dict(_GRADE_DEFAULTS)
    return frappe._dict(values)


def _require_scorecard_access() -> None:
    require_configured_role("scorecard_roles", action=_("view Buyback scorecards"))
    for doctype in ("Buyback Order", "Buyback Assessment", "Buyback Inspection"):
        frappe.has_permission(doctype, ptype="read", throw=True)


def _scorecard_period(from_date=None, to_date=None) -> tuple[str, str]:
    start = getdate(from_date or add_months(nowdate(), -1))
    end = getdate(to_date or nowdate())
    if start > end:
        frappe.throw(_("From Date cannot be after To Date."))
    days = date_diff(end, start) + 1
    max_days = get_int_setting("scorecard_max_range_days", 366)
    if days > max_days:
        frappe.throw(
            _("Scorecard range cannot exceed {0} days.").format(max_days)
        )
    return str(start), str(end)


# ═══════════════════════════════════════════════════════════════════
# STORE SCORECARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_store_scorecards(from_date=None, to_date=None, company=None) -> list:
    """Compute scorecard for all active stores."""
    _require_scorecard_access()
    from_date, to_date = _scorecard_period(from_date, to_date)
    if company:
        assert_buyback_scope(company=company)
    scope_cond, scope_params = build_buyback_scope_sql(
        store_field="store", company_field="company", prefix="store_score"
    )
    params = {
        "from_date": from_date,
        "to_date_end": f"{to_date} 23:59:59",
        "sla_target": DEFAULT_SLAS["confirmation_to_approval"],
        **scope_params,
    }
    company_cond = ""
    if company:
        company_cond = "AND company = %(company)s"
        params["company"] = company

    stores = frappe.db.sql("""
        SELECT store,
            COUNT(*) as total_orders,
            SUM(CASE WHEN status IN ('Paid','Closed') THEN 1 ELSE 0 END) as paid_orders,
            COALESCE(SUM(CASE WHEN status IN ('Paid','Closed') THEN total_paid ELSE 0 END), 0) as total_payout,
            SUM(CASE WHEN status IN ('Rejected','Cancelled') THEN 1 ELSE 0 END) as rejected,
            SUM(CASE WHEN TIMESTAMPDIFF(
                MINUTE, creation, COALESCE(approval_date, CURRENT_TIMESTAMP)
            ) <= %(sla_target)s THEN 1 ELSE 0 END) AS sla_ok
        FROM `tabBuyback Order`
        WHERE docstatus < 2
            AND creation BETWEEN %(from_date)s AND %(to_date_end)s
            AND {scope_cond}
            {company_cond}
        GROUP BY store
    """.format(company_cond=company_cond, scope_cond=scope_cond), params, as_dict=1)  # noqa: UP032

    if not stores:
        return []
    config = _scorecard_config("store_scorecard_configuration")
    grade_thresholds = _grade_thresholds()

    assessment_counts = {
        row.store: row.cnt
        for row in frappe.db.sql("""
            SELECT store, COUNT(*) AS cnt
            FROM `tabBuyback Assessment`
            WHERE docstatus < 2
                AND creation BETWEEN %(from_date)s AND %(to_date_end)s
                AND {scope_cond}
                {company_cond}
            GROUP BY store
        """.format(scope_cond=scope_cond, company_cond=company_cond), params, as_dict=1)  # noqa: UP032
    }
    # Global benchmarks
    global_avg_order = sum(s.total_payout for s in stores) / max(sum(s.paid_orders for s in stores), 1)

    results = []
    for s in stores:
        assessments = assessment_counts.get(s.store) or 1

        conversion_rate = min(s.total_orders / assessments * 100, 100)
        avg_order = s.total_payout / max(s.paid_orders, 1)
        rejection_pct = s.rejected / max(s.total_orders, 1) * 100

        sla_compliance = (s.sla_ok or 0) / max(s.total_orders, 1) * 100

        # Normalised scores (0-100)
        s_conversion = min(conversion_rate / config.conversion_target_pct * 100, 100)
        s_sla = min(sla_compliance, 100)
        s_avg_value = min(avg_order / max(global_avg_order, 1) * 100, 100) if global_avg_order else 50
        s_rejection = max(100 - rejection_pct / config.rejection_zero_pct * 100, 0)

        composite = _weighted_score(
            (s_conversion, s_sla, s_avg_value, s_rejection),
            (
                config.conversion_weight,
                config.sla_weight,
                config.average_value_weight,
                config.rejection_weight,
            ),
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
            "grade": _score_grade(composite, grade_thresholds),
        })

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    return results


# ═══════════════════════════════════════════════════════════════════
# INSPECTOR SCORECARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_inspector_scorecards(from_date=None, to_date=None, store=None) -> list:
    """Compute scorecard for all inspectors."""
    _require_scorecard_access()
    from_date, to_date = _scorecard_period(from_date, to_date)
    if store:
        assert_buyback_scope(store=store)
    scope_cond, scope_params = build_buyback_scope_sql(
        store_field="store", company_field="company", prefix="inspector_score"
    )
    params = {
        "from_date": from_date,
        "to_date_end": f"{to_date} 23:59:59",
        **scope_params,
    }
    store_cond = ""
    if store:
        store_cond = "AND store = %(store)s"
        params["store"] = store

    inspectors = frappe.db.sql("""
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
            DATEDIFF(%(to_date)s, %(from_date)s) + 1 as days_in_range
        FROM `tabBuyback Inspection`
        WHERE creation BETWEEN %(from_date)s AND %(to_date_end)s
            AND {scope_cond}
            {store_cond}
        GROUP BY inspector
    """.format(scope_cond=scope_cond, store_cond=store_cond), {**params, "to_date": to_date}, as_dict=1)  # noqa: UP032

    inspection_scope, inspection_scope_params = build_buyback_scope_sql(
        store_field="i.store", company_field="i.company", prefix="linked_inspection"
    )
    linked_params = {
        "from_date": from_date,
        "to_date_end": f"{to_date} 23:59:59",
        **inspection_scope_params,
    }
    linked_store_cond = ""
    if store:
        linked_store_cond = "AND i.store = %(linked_store)s"
        linked_params["linked_store"] = store
    linked_paid_by_inspector = {
        row.inspector: row.cnt
        for row in frappe.db.sql("""
            SELECT i.inspector, COUNT(o.name) AS cnt
            FROM `tabBuyback Inspection` i
            INNER JOIN `tabBuyback Order` o
                ON o.buyback_inspection = i.name
                AND o.docstatus < 2
                AND o.status IN ('Paid', 'Closed')
            WHERE i.inspector IS NOT NULL
                AND i.creation BETWEEN %(from_date)s AND %(to_date_end)s
                AND {inspection_scope}
                {linked_store_cond}
            GROUP BY i.inspector
        """.format(
            inspection_scope=inspection_scope,
            linked_store_cond=linked_store_cond,
        ), linked_params, as_dict=1)  # noqa: UP032
    }
    config = _scorecard_config("inspector_scorecard_configuration")
    grade_thresholds = _grade_thresholds()

    results = []
    for insp in inspectors:
        if not insp.inspector:
            continue

        items_per_day = insp.completed / max(insp.days_in_range or 1, 1)
        grade_a_pct = insp.grade_a_count / max(insp.total_inspections, 1) * 100

        linked_paid = linked_paid_by_inspector.get(insp.inspector, 0)
        order_conversion = linked_paid / max(insp.completed, 1) * 100

        # Scoring
        s_throughput = min(items_per_day / config.daily_target * 100, 100)
        avg_duration = flt(insp.avg_duration_min) or config.duration_target_minutes
        s_speed = min(config.duration_target_minutes / max(avg_duration, 1) * 100, 100)
        s_grade_a = min(grade_a_pct / config.grade_a_target_pct * 100, 100)
        s_conversion = min(order_conversion / config.conversion_target_pct * 100, 100)

        composite = _weighted_score(
            (s_throughput, s_speed, s_grade_a, s_conversion),
            (
                config.throughput_weight,
                config.speed_weight,
                config.grade_a_weight,
                config.conversion_weight,
            ),
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
            "grade": _score_grade(composite, grade_thresholds),
        })

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    return results


# ═══════════════════════════════════════════════════════════════════
# EXECUTIVE SCORECARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_executive_scorecards(from_date=None, to_date=None, store=None) -> list:
    """Compute scorecard for buyback agents/executives."""
    _require_scorecard_access()
    from_date, to_date = _scorecard_period(from_date, to_date)
    if store:
        assert_buyback_scope(store=store)
    scope_cond, scope_params = build_buyback_scope_sql(
        store_field="store", company_field="company", prefix="executive_score"
    )
    params = {
        "from_date": from_date,
        "to_date_end": f"{to_date} 23:59:59",
        "sla_target": DEFAULT_SLAS["confirmation_to_approval"],
        **scope_params,
    }
    store_cond = ""
    if store:
        store_cond = "AND store = %(store)s"
        params["store"] = store

    executives = frappe.db.sql("""
        SELECT
            owner as executive,
            COUNT(*) as total_orders,
            SUM(CASE WHEN status IN ('Paid','Closed') THEN 1 ELSE 0 END) as paid_orders,
            COALESCE(SUM(CASE WHEN status IN ('Paid','Closed') THEN total_paid ELSE 0 END), 0) as total_payout,
            SUM(CASE WHEN status IN ('Rejected','Cancelled') THEN 1 ELSE 0 END) as rejected,
            SUM(CASE WHEN TIMESTAMPDIFF(
                MINUTE, creation, COALESCE(approval_date, CURRENT_TIMESTAMP)
            ) <= %(sla_target)s THEN 1 ELSE 0 END) AS sla_ok
        FROM `tabBuyback Order`
        WHERE docstatus < 2
            AND creation BETWEEN %(from_date)s AND %(to_date_end)s
            AND {scope_cond}
            {store_cond}
        GROUP BY owner
    """.format(scope_cond=scope_cond, store_cond=store_cond), params, as_dict=1)  # noqa: UP032

    assessment_scope, assessment_scope_params = build_buyback_scope_sql(
        store_field="store", company_field="company", prefix="executive_assessment"
    )
    assessment_store_cond = ""
    assessment_params = {
        "from_date": from_date,
        "to_date_end": f"{to_date} 23:59:59",
        **assessment_scope_params,
    }
    if store:
        assessment_store_cond = "AND store = %(assessment_store)s"
        assessment_params["assessment_store"] = store
    assessments_by_owner = {
        row.executive: row.cnt
        for row in frappe.db.sql("""
            SELECT owner AS executive, COUNT(*) AS cnt
            FROM `tabBuyback Assessment`
            WHERE docstatus < 2
                AND creation BETWEEN %(from_date)s AND %(to_date_end)s
                AND {assessment_scope}
                {assessment_store_cond}
            GROUP BY owner
        """.format(
            assessment_scope=assessment_scope,
            assessment_store_cond=assessment_store_cond,
        ), assessment_params, as_dict=1)  # noqa: UP032
    }
    config = _scorecard_config("executive_scorecard_configuration")
    grade_thresholds = _grade_thresholds()
    results = []
    for ex in executives:
        if not ex.executive:
            continue

        assessments = assessments_by_owner.get(ex.executive) or 1

        conversion = min(ex.paid_orders / assessments * 100, 100)
        rejection_pct = ex.rejected / max(ex.total_orders, 1) * 100

        sla_compliance = (ex.sla_ok or 0) / max(ex.total_orders, 1) * 100

        # Scoring
        s_orders = min(ex.total_orders / config.order_target * 100, 100)
        s_conversion = min(conversion / config.conversion_target_pct * 100, 100)
        s_sla = min(sla_compliance, 100)
        s_satisfaction = max(100 - rejection_pct / config.rejection_zero_pct * 100, 0)

        composite = _weighted_score(
            (s_orders, s_conversion, s_sla, s_satisfaction),
            (
                config.orders_weight,
                config.conversion_weight,
                config.sla_weight,
                config.satisfaction_weight,
            ),
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
            "grade": _score_grade(composite, grade_thresholds),
        })

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    return results


# ─── Helpers ─────────────────────────────────────────────────────────

def _score_grade(score, thresholds=None):
    """Convert numeric score to letter grade."""
    thresholds = thresholds or _grade_thresholds()
    if score >= thresholds.a_plus:
        return "A+"
    elif score >= thresholds.a:
        return "A"
    elif score >= thresholds.b:
        return "B"
    elif score >= thresholds.c:
        return "C"
    elif score >= thresholds.d:
        return "D"
    else:
        return "F"
