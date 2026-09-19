# Copyright (c) 2026, GoStack and contributors
# Dashboard API — Backend data for all persona dashboards.
# Rebuilt to reflect the unified buyback + exchange + assessment flow.
# B2 gap closure: all queries use parameterized %(key)s placeholders.

import frappe
from frappe import _
from frappe.utils import add_days, add_months, date_diff, flt, getdate, nowdate

from buyback.utils import (
    assert_buyback_scope,
    build_buyback_scope_sql,
    get_int_setting,
    require_configured_role,
)


def _date_params(from_date, to_date):
    """Return standard date parameters for queries."""
    return {"from_date": from_date, "to_date_end": f"{to_date} 23:59:59"}


def _build_params(from_date, to_date, col="creation", alias="", scope_prefix="dashboard", **kwargs):
    """Build a params dict and a list of SQL AND-clauses from optional filters.

    Returns (sql_conditions_str, params_dict).
    The date range clause is always included.
    Optional filters (company, store, brand, item_group) are added only when truthy.
    """
    params = _date_params(from_date, to_date)
    clauses = [f"{alias}{col} BETWEEN %(from_date)s AND %(to_date_end)s"]

    field_map = {
        "company": f"{alias}company",
        "store": f"{alias}store",
        "brand": f"{alias}brand",
        "item_group": f"{alias}item_group",
    }

    for key, db_field in field_map.items():
        value = kwargs.get(key)
        if value:
            params[key] = value
            clauses.append(f"{db_field} = %({key})s")

    scope_clause, scope_params = build_buyback_scope_sql(
        store_field=f"{alias}store",
        company_field=f"{alias}company",
        prefix=scope_prefix,
    )
    clauses.append(scope_clause)
    params.update(scope_params)

    return " AND ".join(clauses), params


def _check_dashboard_access():
    """Ensure caller has at least read access to Buyback Order."""
    for doctype in (
        "Buyback Order", "Buyback Assessment", "Buyback Inspection",
        "Buyback SLA Log", "Buyback Audit Log",
    ):
        if not frappe.has_permission(doctype, "read"):
            frappe.throw(
                _("You do not have permission to view {0}.").format(doctype),
                frappe.PermissionError,
                title=_("API Error"),
            )


def _validate_date_range(from_date, to_date):
    start = getdate(from_date)
    end = getdate(to_date)
    if end < start:
        frappe.throw(_("To Date cannot be before From Date."))
    max_days = min(get_int_setting("scorecard_max_range_days", 366), 730)
    if date_diff(end, start) > max_days:
        frappe.throw(_("Dashboard date range cannot exceed {0} days.").format(max_days))
    return start, end


# ═══════════════════════════════════════════════════════════════════
# STORE MANAGER DASHBOARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_store_dashboard(store=None, from_date=None, to_date=None) -> dict:
    """Store Manager — Branch-level performance with pending action counts."""
    _check_dashboard_access()
    from_date = from_date or nowdate()
    to_date = to_date or nowdate()
    from_date, to_date = _validate_date_range(from_date, to_date)
    if not store:
        frappe.throw(_("Store is required"), title=_("API Error"))
    assert_buyback_scope(store=store)

    where, params = _build_params(from_date, to_date, store=store, scope_prefix="store_dashboard")

    # KPIs
    o_row = frappe.db.sql("""
        SELECT
            COUNT(*) as total_orders,
            SUM(CASE WHEN status IN ('Paid','Closed') THEN 1 ELSE 0 END) as settled,
            COALESCE(SUM(CASE WHEN status IN ('Paid','Closed') THEN total_paid ELSE 0 END), 0) as total_payout,
            SUM(CASE WHEN status IN ('Draft','Awaiting Approval','Awaiting OTP','Awaiting Customer Approval') THEN 1 ELSE 0 END) as pending
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND {where}
    """.format(where=where), params, as_dict=1)[0]  # noqa: UP032

    # Source mix for this store
    src = frappe.db.sql("""
        SELECT
            SUM(CASE WHEN source='App Diagnosis' THEN 1 ELSE 0 END) as app_cnt,
            COUNT(*) as total
        FROM `tabBuyback Assessment`
        WHERE {where}
    """.format(where=where), params, as_dict=1)[0]  # noqa: UP032

    # Pending action counts
    pending_inspection = frappe.db.count("Buyback Assessment", {
        "store": store, "status": "Submitted", "docstatus": ("<", 2),
        "creation": ("between", [from_date, f"{to_date} 23:59:59"]),
    })
    pending_approval = frappe.db.count("Buyback Order", {
        "store": store, "status": ["in", ["Awaiting Approval", "Awaiting Customer Approval"]],
        "docstatus": ("<", 2),
    })
    pending_settlement = frappe.db.count("Buyback Order", {
        "store": store, "status": ["in", ["Approved", "Customer Approved", "OTP Verified"]],
        "docstatus": ("<", 2),
        "total_paid": ["in", [None, 0]],
    })

    # SLA breaches
    sla_breaches = frappe.db.count("Buyback SLA Log", {
        "store": store, "breached": 1, "creation": ("between", [from_date, f"{to_date} 23:59:59"]),
    })

    # SLA compliance
    sla_total = frappe.db.count("Buyback SLA Log", {
        "store": store, "creation": ("between", [from_date, f"{to_date} 23:59:59"]),
    })
    sla_compliance = round((1 - (sla_breaches or 0) / max(sla_total, 1)) * 100, 1) if sla_total else 100.0

    # Pending pickups (paid but not yet closed)
    pending_pickups = frappe.db.count("Buyback Order", {
        "store": store, "status": "Paid", "docstatus": ("<", 2),
        "settlement_type": ["in", ["Buyback", None, ""]],
    })

    # Top models
    top_models = frappe.db.sql("""
        SELECT item, COUNT(*) as qty, COALESCE(SUM(final_price), 0) as value
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND {where}
        GROUP BY item ORDER BY qty DESC LIMIT 5
    """.format(where=where), params, as_dict=1)  # noqa: UP032

    return {
        "kpis": {
            "total_orders": o_row.total_orders or 0,
            "paid": o_row.settled or 0,
            "total_payout": o_row.total_payout or 0,
            "pending": o_row.pending or 0,
            "app_quote_pct": round((src.app_cnt or 0) / max((src.total or 0), 1) * 100, 1),
            "pending_inspection": pending_inspection,
            "pending_approvals": pending_approval,
            "pending_payments": pending_settlement,
            "sla_breaches": sla_breaches,
            "sla_compliance": sla_compliance,
            "pending_pickups": pending_pickups,
        },
        "top_models": top_models,
    }


# ═══════════════════════════════════════════════════════════════════
# DASHBOARD DRILL-DOWN — one registry for every buyback card
#
# Every card must open onto the rows it counted. The risk is not the SQL, it is
# the predicates drifting apart, and two traps are already live in here:
#
#   * On the Store dashboard, `pending_approvals`, `pending_payments` and
#     `pending_pickups` are NOT date-filtered — they count everything
#     outstanding, whatever range is on screen. A drill-down that helpfully
#     applied the range would return fewer rows than the card claims.
#   * Finance's pending-payout card counts the same STATUS set as the Store
#     dashboard's `pending_payments`, but IS date-filtered. Same words on two
#     screens, two different populations.
#
# Stating each predicate once here is what keeps the number and the list
# together; test_buyback_dashboard_drilldown asserts card == len(rows) for
# every entry, against records it seeds and rolls back.
# ═══════════════════════════════════════════════════════════════════

_ORDER = "Buyback Order"
_ASSESS = "Buyback Assessment"
_SLA = "Buyback SLA Log"

_PAID = "status IN ('Paid','Closed')"
_AWAIT_APPROVAL = "status IN ('Awaiting Approval','Awaiting Customer Approval')"
_UNPAID_APPROVED = ("status IN ('Approved','Customer Approved','OTP Verified') "
                    "AND IFNULL(total_paid, 0) = 0")

#: (dashboard, tile) -> (doctype, extra predicate or None, honours date range?)
BUYBACK_TILES: dict[tuple[str, str], tuple[str, str | None, bool]] = {
    # ── Store Manager ────────────────────────────────────────────────
    ("store", "total_orders"):      (_ORDER, None, True),
    ("store", "paid"):              (_ORDER, _PAID, True),
    ("store", "total_payout"):      (_ORDER, _PAID, True),
    ("store", "pending"):           (_ORDER,
                                     "status IN ('Draft','Awaiting Approval','Awaiting OTP',"
                                     "'Awaiting Customer Approval')", True),
    ("store", "pending_approvals"): (_ORDER, _AWAIT_APPROVAL, False),
    ("store", "pending_payments"):  (_ORDER, _UNPAID_APPROVED, False),
    ("store", "pending_pickups"):   (_ORDER,
                                     "status = 'Paid' AND IFNULL(settlement_type, '') "
                                     "IN ('Buyback', '')", False),
    ("store", "pending_inspection"): (_ASSESS, "status = 'Submitted'", True),
    ("store", "sla_breaches"):      (_SLA, "breached = 1", True),

    # ── Operations ───────────────────────────────────────────────────
    ("operations", "sla_on_time"):  (_SLA, "IFNULL(breached, 0) = 0", True),
    ("operations", "sla_breaches"): (_SLA, "breached = 1", True),

    # ── Finance ──────────────────────────────────────────────────────
    # Card keys as the page renders them. "total_paid" is a CURRENCY card —
    # the sum of total_paid over the same rows — not a count.
    ("finance", "total_paid"):      (_ORDER, _PAID, True),
    # Same status set as store/pending_payments, but date-filtered. Not a typo:
    # the two screens deliberately answer over different windows.
    ("finance", "pending_count"):   (_ORDER, _UNPAID_APPROVED, True),
    ("finance", "pending_amount"):  (_ORDER, _UNPAID_APPROVED, True),

    # ── Compliance ───────────────────────────────────────────────────
    # The remaining four cards need shapes the generic builder cannot express
    # and are handled by _compliance_sql below. "large_payout_threshold" is a
    # setting, not a population, and is deliberately not openable.
    ("compliance", "high_value_orders"): (_ORDER, None, True),
    ("compliance", "high_value_total"):  (_ORDER, None, True),
}

#: Compliance cards served by _compliance_sql rather than the generic builder.
_COMPLIANCE_CUSTOM = {
    "manager_overrides", "manual_approvals", "auto_approvals", "duplicate_imeis",
}

#: Cards whose number is a money total rather than a row count. The list is the
#: same rows either way; the test sums instead of counting.
CURRENCY_TILES = {
    ("store", "total_payout"): "total_paid",
    ("finance", "total_paid"): "total_paid",
    ("finance", "pending_amount"): "final_price",
    ("compliance", "high_value_total"): "total_paid",
}

#: Compliance cards whose shape the generic single-table builder cannot express.
#: Each returns (doctype, sql, params) and must yield exactly as many rows as the
#: card counts — see the notes on each.
_AUDIT_OVERRIDE_ACTIONS = "('Price Override', 'Grade Changed')"
_AUDIT_MANUAL_ACTIONS = "('Manual Approval', 'Price Override', 'Grade Changed')"
_APPROVED_ANY = "status IN ('Paid','Closed','Approved','Customer Approved','OTP Verified')"


def _compliance_sql(tile, company, from_date, to_date):
    """SQL for the compliance cards that are not a plain filtered table scan."""
    params = dict(_date_params(from_date, to_date))
    co_order, co_assess = "", ""
    if company:
        params["company"] = company
        co_order = " AND o.company = %(company)s"
        co_assess = " AND a.company = %(company)s"

    if tile == "manager_overrides":
        # The card counts DISTINCT audit-log rows, so the list is log rows.
        return ("Buyback Audit Log", f"""
            SELECT DISTINCT a.name, a.action, a.reference_name, a.owner, a.creation
            FROM `tabBuyback Audit Log` a
            INNER JOIN `tabBuyback Order` o
                ON a.reference_doctype = 'Buyback Order' AND a.reference_name = o.name
            WHERE a.action IN {_AUDIT_OVERRIDE_ACTIONS}
              AND a.creation BETWEEN %(from_date)s AND %(to_date_end)s{co_order}
            ORDER BY a.creation DESC
        """, params)

    if tile == "manual_approvals":
        # Counts DISTINCT reference_name — so the list is ORDERS, not log rows.
        return (_ORDER, f"""
            SELECT DISTINCT o.name, o.customer_name, o.item, o.status,
                   o.final_price, o.total_paid, o.customer_payout_mode, o.creation
            FROM `tabBuyback Order` o
            INNER JOIN `tabBuyback Audit Log` a
                ON a.reference_doctype = 'Buyback Order' AND a.reference_name = o.name
            WHERE a.action IN {_AUDIT_MANUAL_ACTIONS}
              AND a.creation BETWEEN %(from_date)s AND %(to_date_end)s{co_order}
            ORDER BY o.creation DESC
        """, params)

    if tile == "auto_approvals":
        # Derived by subtraction on the card: approved orders MINUS the manually
        # touched ones. Expressed here as the same set difference.
        return (_ORDER, f"""
            SELECT o.name, o.customer_name, o.item, o.status,
                   o.final_price, o.total_paid, o.customer_payout_mode, o.creation
            FROM `tabBuyback Order` o
            WHERE o.docstatus < 2 AND o.{_APPROVED_ANY}
              AND o.creation BETWEEN %(from_date)s AND %(to_date_end)s{co_order}
              AND o.name NOT IN (
                  SELECT a.reference_name FROM `tabBuyback Audit Log` a
                  WHERE a.reference_doctype = 'Buyback Order'
                    AND a.action IN {_AUDIT_MANUAL_ACTIONS}
                    AND a.creation BETWEEN %(from_date)s AND %(to_date_end)s
              )
            ORDER BY o.creation DESC
        """, params)

    if tile == "duplicate_imeis":
        # The card counts IMEI VALUES appearing more than once, not assessments.
        # One row per duplicated IMEI keeps count == len(rows) honest, and the
        # occurrence count is the useful thing to show next to it.
        return ("Buyback Assessment", f"""
            SELECT a.imei_serial AS name, COUNT(*) AS occurrences,
                   MIN(a.creation) AS first_seen, MAX(a.creation) AS creation
            FROM `tabBuyback Assessment` a
            WHERE IFNULL(a.imei_serial, '') != ''
              AND a.creation BETWEEN %(from_date)s AND %(to_date_end)s{co_assess}
            GROUP BY a.imei_serial HAVING COUNT(*) > 1
            ORDER BY occurrences DESC
        """, params)

    return None

_TILE_COLUMNS = {
    _ORDER: ["name", "customer_name", "item", "status", "final_price", "total_paid",
             "customer_payout_mode", "creation"],
    _ASSESS: ["name", "customer_name", "status", "source", "store", "creation"],
    # Buyback SLA Log names its stage `sla_stage`, not `stage`.
    _SLA: ["name", "sla_stage", "reference_name", "breached", "exceeded_by", "creation"],
}

DRILLDOWN_LIMIT = 500


def _high_value_threshold() -> float:
    return flt(frappe.db.get_single_value(
        "Buyback SLA Settings", "large_payout_threshold")) or 25000


def _tile_where(dashboard: str, tile: str, store, company, from_date, to_date):
    doctype, extra, dated = BUYBACK_TILES[(dashboard, tile)]
    if dashboard == "compliance" and tile in ("high_value_orders", "high_value_total"):
        # Threshold comes from Buyback SLA Settings, so it cannot sit in the
        # static registry.
        extra = f"{_PAID} AND total_paid > {flt(_high_value_threshold())}"
    params: dict = {}
    clauses = []

    if doctype != _SLA:
        clauses.append("docstatus < 2")
    if store:
        params["store"] = store
        clauses.append("store = %(store)s")
    if company and doctype == _ORDER:
        params["company"] = company
        clauses.append("company = %(company)s")
    if dated:
        params.update(_date_params(from_date, to_date))
        clauses.append("creation BETWEEN %(from_date)s AND %(to_date_end)s")
    if extra:
        clauses.append(f"({extra})")

    scope_clause, scope_params = build_buyback_scope_sql(
        store_field="store", company_field="company",
        prefix=f"tile_{dashboard}_{tile}")
    clauses.append(scope_clause)
    params.update(scope_params)
    return doctype, " AND ".join(clauses), params


@frappe.whitelist()
def get_dashboard_tile_rows(dashboard: str, tile: str, store: str | None = None,
                            company: str | None = None,
                            from_date=None, to_date=None) -> dict:
    """The rows behind one buyback dashboard card.

    Same access check, same scope and the same predicate that produced the
    number — see BUYBACK_TILES.
    """
    _check_dashboard_access()
    key = (dashboard, tile)
    if key not in BUYBACK_TILES and not (
            dashboard == "compliance" and tile in _COMPLIANCE_CUSTOM):
        frappe.throw(
            _("This dashboard has no {0} card. Refresh the page to see the current cards.")
            .format(frappe.bold(tile)),
            title=_("Page Out Of Date"),
        )
    from_date = from_date or nowdate()
    to_date = to_date or nowdate()
    from_date, to_date = _validate_date_range(from_date, to_date)
    if store:
        assert_buyback_scope(store=store)

    custom = (_compliance_sql(tile, company, from_date, to_date)
              if dashboard == "compliance" and tile in _COMPLIANCE_CUSTOM else None)
    if custom:
        doctype, sql, params = custom
        rows = frappe.db.sql(f"{sql} LIMIT {DRILLDOWN_LIMIT + 1}", params, as_dict=1)
        columns = list(rows[0].keys()) if rows else ["name"]
    else:
        doctype, where, params = _tile_where(
            dashboard, tile, store, company, from_date, to_date)
        columns = _TILE_COLUMNS[doctype]
        rows = frappe.db.sql(
            "SELECT {cols} FROM `tab{dt}` WHERE {where} ORDER BY creation DESC LIMIT {lim}".format(
                cols=", ".join(f"`{c}`" for c in columns), dt=doctype, where=where,
                lim=DRILLDOWN_LIMIT + 1),
            params, as_dict=1)
    truncated = len(rows) > DRILLDOWN_LIMIT
    rows = rows[:DRILLDOWN_LIMIT]
    return {
        "dashboard": dashboard, "tile": tile, "doctype": doctype,
        "columns": columns, "rows": rows,
        "total": len(rows), "truncated": truncated,
    }


# ═══════════════════════════════════════════════════════════════════
# CATEGORY MANAGER DASHBOARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_category_dashboard(from_date=None, to_date=None, brand=None, item_group=None) -> dict:
    """Category Manager — Model/brand performance & mismatch hotspots."""
    _check_dashboard_access()
    from_date = from_date or add_months(nowdate(), -1)
    to_date = to_date or nowdate()
    from_date, to_date = _validate_date_range(from_date, to_date)

    where, params = _build_params(
        from_date, to_date, brand=brand, item_group=item_group, scope_prefix="category_assessment"
    )

    # Build a separate where clause for Buyback Order (no item_group column)
    order_where, order_params = _build_params(
        from_date, to_date, brand=brand, scope_prefix="category_order"
    )
    order_where_aliased, order_params_aliased = _build_params(
        from_date, to_date, alias="o.", brand=brand, scope_prefix="category_order_alias"
    )

    # Top categories
    top_cats = frappe.db.sql("""
        SELECT item_group, COUNT(*) as cnt, COALESCE(SUM(IFNULL(quoted_price, estimated_price)),0) as value
        FROM `tabBuyback Assessment` WHERE {where}
        GROUP BY item_group ORDER BY cnt DESC LIMIT 10
    """.format(where=where), params, as_dict=1)  # noqa: UP032

    # Top brands (for bar chart)
    brand_data = frappe.db.sql("""
        SELECT brand, COUNT(*) as qty, COALESCE(SUM(IFNULL(quoted_price, estimated_price)),0) as value
        FROM `tabBuyback Assessment` WHERE {where}
        GROUP BY brand ORDER BY qty DESC LIMIT 10
    """.format(where=where), params, as_dict=1)  # noqa: UP032

    # Model-wise inflow
    model_inflow = frappe.db.sql("""
        SELECT item, brand, item_group, COUNT(*) as cnt,
            ROUND(AVG(IFNULL(quoted_price, estimated_price)),0) as avg_price,
            ROUND(SUM(CASE WHEN source='App Diagnosis' THEN 1 ELSE 0 END) / COUNT(*) * 100, 1) as app_pct
        FROM `tabBuyback Assessment` WHERE {where}
        GROUP BY item, brand, item_group ORDER BY cnt DESC LIMIT 20
    """.format(where=where), params, as_dict=1)  # noqa: UP032

    # Mismatch hotspots (models with highest mismatch) — uses aliased columns
    mm_where = order_where_aliased

    mismatch_hotspots = frappe.db.sql("""
        SELECT o.item, o.brand,
            ROUND(AVG(i.mismatch_percentage),1) as avg_mismatch,
            ROUND(AVG(ABS(IFNULL(o.price_variance_pct,0))),1) as avg_price_var,
            COUNT(*) as cnt
        FROM `tabBuyback Order` o
        JOIN `tabBuyback Inspection` i ON i.name = o.buyback_inspection
        WHERE o.docstatus < 2 AND {where}
            AND i.mismatch_percentage > 0
        GROUP BY o.item, o.brand
        ORDER BY avg_mismatch DESC
        LIMIT 10
    """.format(where=mm_where), order_params_aliased, as_dict=1)  # noqa: UP032

    # Grade mix (for pie chart) — uses order_where (no item_group on Buyback Order)
    grade_data = frappe.db.sql("""
        SELECT
            o.condition_grade AS grade_id,
            COALESCE(gm.grade_name, o.condition_grade) AS grade,
            COUNT(*) AS qty
        FROM `tabBuyback Order` o
        LEFT JOIN `tabGrade Master` gm ON gm.name = o.condition_grade
        WHERE o.docstatus < 2 AND {where} AND o.condition_grade IS NOT NULL
        GROUP BY o.condition_grade, gm.grade_name
        ORDER BY qty DESC
    """.format(where=order_where_aliased), order_params_aliased, as_dict=1)  # noqa: UP032

    # Settlement mix by brand — uses order_where (no item_group on Buyback Order)
    settlement_by_brand = frappe.db.sql("""
        SELECT brand,
            SUM(CASE WHEN IFNULL(settlement_type,'Buyback')='Buyback' THEN 1 ELSE 0 END) as buyback,
            SUM(CASE WHEN settlement_type='Exchange' THEN 1 ELSE 0 END) as exchange
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed') AND {where}
        GROUP BY brand ORDER BY COUNT(*) DESC LIMIT 10
    """.format(where=order_where), order_params, as_dict=1)  # noqa: UP032

    # Monthly price trend (for line chart)
    price_trend = frappe.db.sql("""
        SELECT DATE_FORMAT(creation, '%%Y-%%m') as month,
            ROUND(AVG(IFNULL(quoted_price, estimated_price)), 0) as avg_price
        FROM `tabBuyback Assessment`
        WHERE {where}
        GROUP BY month ORDER BY month
    """.format(where=where), params, as_dict=1)  # noqa: UP032

    # Top depreciating models — compare first-half vs second-half avg price
    mid_date = add_days(from_date, date_diff(to_date, from_date) // 2)
    depreciation = frappe.db.sql("""
        SELECT * FROM (
            SELECT item, brand,
                ROUND(AVG(CASE WHEN creation < %(mid_date)s
                    THEN IFNULL(quoted_price, estimated_price) END), 0) as old_price,
                ROUND(AVG(CASE WHEN creation >= %(mid_date)s
                    THEN IFNULL(quoted_price, estimated_price) END), 0) as new_price
            FROM `tabBuyback Assessment`
            WHERE {where}
            GROUP BY item, brand
        ) t
        WHERE t.old_price IS NOT NULL AND t.new_price IS NOT NULL AND t.new_price < t.old_price
        ORDER BY (t.old_price - t.new_price) / t.old_price DESC
        LIMIT 10
    """.format(where=where), {**params, "mid_date": str(mid_date)}, as_dict=1)  # noqa: UP032

    # Compute depreciation percentage in Python
    for row in depreciation:
        row["depreciation_pct"] = round((row.old_price - row.new_price) / max(row.old_price, 1) * 100, 1)

    return {
        "top_categories": top_cats,
        "brand_data": brand_data,
        "model_inflow": model_inflow,
        "mismatch_hotspots": mismatch_hotspots,
        "grade_data": grade_data,
        "settlement_by_brand": settlement_by_brand,
        "price_trend": price_trend,
        "depreciation": depreciation,
    }


# ═══════════════════════════════════════════════════════════════════
# FINANCE DASHBOARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_finance_dashboard(from_date=None, to_date=None, company=None) -> dict:
    """Finance — Payouts, pending settlements, exchange adjustments."""
    _check_dashboard_access()
    from_date = from_date or add_months(nowdate(), -1)
    to_date = to_date or nowdate()
    from_date, to_date = _validate_date_range(from_date, to_date)

    if company:
        assert_buyback_scope(company=company)

    where, params = _build_params(
        from_date, to_date, company=company, scope_prefix="finance_dashboard"
    )

    # Payout KPIs
    p_row = frappe.db.sql("""
        SELECT
            COUNT(*) as total_paid,
            COALESCE(SUM(total_paid), 0) as total_amount,
            SUM(CASE WHEN customer_payout_mode='Cash' THEN 1 ELSE 0 END) as cash_count,
            COALESCE(SUM(CASE WHEN customer_payout_mode='Cash' THEN total_paid ELSE 0 END), 0) as cash_amount,
            SUM(CASE WHEN customer_payout_mode='Bank Transfer' THEN 1 ELSE 0 END) as bank_count,
            SUM(CASE WHEN customer_payout_mode='UPI' THEN 1 ELSE 0 END) as upi_count
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed') AND {where}
    """.format(where=where), params, as_dict=1)[0]  # noqa: UP032

    # Pending payouts
    pending = frappe.db.sql("""
        SELECT COUNT(*) as cnt, COALESCE(SUM(final_price), 0) as amount
        FROM `tabBuyback Order`
        WHERE docstatus < 2
            AND status IN ('Approved','Customer Approved','OTP Verified')
            AND (total_paid IS NULL OR total_paid = 0)
            AND {where}
    """.format(where=where), params, as_dict=1)[0]  # noqa: UP032

    # Exchange adjustments
    ex = frappe.db.sql("""
        SELECT COUNT(*) as cnt,
            COALESCE(SUM(exchange_discount), 0) as adj_value,
            COALESCE(SUM(balance_to_pay), 0) as balance_due
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND settlement_type='Exchange'
            AND status IN ('Paid','Closed') AND {where}
    """.format(where=where), params, as_dict=1)[0]  # noqa: UP032

    # Branch-wise CASH payout (cash risk monitoring)
    branch_cash = frappe.db.sql("""
        SELECT store,
            COUNT(*) as `count`,
            COALESCE(SUM(total_paid), 0) as total_amount,
            COALESCE(AVG(total_paid), 0) as avg_amount
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed')
            AND customer_payout_mode = 'Cash' AND {where}
        GROUP BY store ORDER BY total_amount DESC LIMIT 10
    """.format(where=where), params, as_dict=1)  # noqa: UP032

    # Payment mode mix (for pie chart)
    payment_by_method = frappe.db.sql("""
        SELECT IFNULL(customer_payout_mode,'Unknown') as method, COUNT(*) as `count`,
            COALESCE(SUM(total_paid), 0) as amount
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed') AND {where}
        GROUP BY customer_payout_mode ORDER BY `count` DESC
    """.format(where=where), params, as_dict=1)  # noqa: UP032

    # Daily payouts (for bar chart)
    daily_payouts = frappe.db.sql("""
        SELECT DATE(creation) as date, COALESCE(SUM(total_paid), 0) as amount
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed') AND {where}
        GROUP BY DATE(creation) ORDER BY date
    """.format(where=where), params, as_dict=1)  # noqa: UP032

    high_value_threshold = flt(
        frappe.db.get_single_value("Buyback SLA Settings", "large_payout_threshold")
    ) or 50000
    high_value = frappe.db.sql("""
        SELECT name, store, customer_name, total_paid, customer_payout_mode, customer_payout_updated_at as payment_date
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed')
            AND total_paid > %(high_value_threshold)s AND {where}
        ORDER BY total_paid DESC LIMIT 20
    """.format(where=where), {**params, "high_value_threshold": high_value_threshold}, as_dict=1)  # noqa: UP032

    # Build payment methods summary string
    methods_parts = [f"{m.method}: {m['count']}" for m in payment_by_method if m.method != 'Unknown']
    payment_methods_str = " / ".join(methods_parts) if methods_parts else "None"

    return {
        "kpis": {
            "total_paid": p_row.total_amount or 0,
            "total_paid_count": p_row.total_paid or 0,
            "cash_count": p_row.cash_count or 0,
            "cash_amount": p_row.cash_amount or 0,
            "bank_count": p_row.bank_count or 0,
            "upi_count": p_row.upi_count or 0,
            "pending_count": pending.cnt or 0,
            "pending_amount": pending.amount or 0,
            "exchange_count": ex.cnt or 0,
            "exchange_adj_value": ex.adj_value or 0,
            "exchange_balance_due": ex.balance_due or 0,
            "payment_methods": payment_methods_str,
        },
        "branch_cash": branch_cash,
        "payment_by_method": payment_by_method,
        "daily_payouts": daily_payouts,
        "high_value": high_value,
    }


# ═══════════════════════════════════════════════════════════════════
# COMPLIANCE / QA DASHBOARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_compliance_dashboard(from_date=None, to_date=None, company=None) -> dict:
    """Compliance — Anomalies, OTP failures, mismatches, overrides."""
    _check_dashboard_access()
    from_date = from_date or add_months(nowdate(), -1)
    to_date = to_date or nowdate()
    from_date, to_date = _validate_date_range(from_date, to_date)

    if company:
        assert_buyback_scope(company=company)

    where, params = _build_params(
        from_date, to_date, company=company, scope_prefix="compliance_dashboard"
    )
    date_params = _date_params(from_date, to_date)
    order_scope, order_scope_params = build_buyback_scope_sql(
        store_field="o.store", company_field="o.company", prefix="compliance_order"
    )
    assessment_where, assessment_params = _build_params(
        from_date,
        to_date,
        company=company,
        scope_prefix="compliance_assessment",
    )
    inspection_where, inspection_params = _build_params(
        from_date,
        to_date,
        company=company,
        scope_prefix="compliance_inspection",
    )
    scoped_date_params = {**date_params, **order_scope_params}
    kpis = {}

    # OTP failures
    otp_row = frappe.db.sql("""
        SELECT
            COUNT(*) as total_otp,
            SUM(CASE WHEN otp.status IN ('Failed','Expired') THEN 1 ELSE 0 END) as failures
        FROM `tabCH OTP Log` otp
        INNER JOIN `tabBuyback Order` o
            ON otp.reference_doctype = 'Buyback Order'
            AND otp.reference_name = o.name
        WHERE otp.creation BETWEEN %(from_date)s AND %(to_date_end)s
            AND {order_scope}
    """.format(order_scope=order_scope), scoped_date_params, as_dict=1)[0]  # noqa: UP032
    kpis["otp_total"] = otp_row.total_otp or 0
    kpis["otp_failures"] = otp_row.failures or 0
    kpis["otp_failure_rate"] = round(
        (otp_row.failures or 0) / max((otp_row.total_otp or 0), 1) * 100, 1)

    # Missing approvals (paid without customer_approved)
    kpis["paid_without_approval"] = frappe.db.sql("""
        SELECT COUNT(*)
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed')
            AND customer_approved != 1
            AND buyback_assessment IS NOT NULL AND buyback_assessment != ''
            AND {where}
    """.format(where=where), params)[0][0] or 0  # noqa: UP032

    # Duplicate IMEI
    kpis["duplicate_imeis"] = frappe.db.sql("""
        SELECT COUNT(*) FROM (
            SELECT imei_serial FROM `tabBuyback Assessment`
            WHERE imei_serial IS NOT NULL AND imei_serial != ''
                AND {assessment_where}
            GROUP BY imei_serial HAVING COUNT(*) > 1
        ) dup
    """.format(assessment_where=assessment_where), assessment_params)[0][0] or 0  # noqa: UP032

    # Manager overrides
    kpis["manager_overrides"] = frappe.db.sql("""
        SELECT COUNT(DISTINCT a.name)
        FROM `tabBuyback Audit Log` a
        INNER JOIN `tabBuyback Order` o
            ON a.reference_doctype = 'Buyback Order' AND a.reference_name = o.name
        WHERE a.action IN ('Price Override', 'Grade Changed')
            AND a.creation BETWEEN %(from_date)s AND %(to_date_end)s
            AND {order_scope}
    """.format(order_scope=order_scope), scoped_date_params)[0][0] or 0  # noqa: UP032

    # High-value orders
    threshold = flt(frappe.db.get_single_value("Buyback SLA Settings", "large_payout_threshold")) or 25000
    kpis["large_payout_threshold"] = threshold
    hv = frappe.db.sql("""
        SELECT COUNT(*) as cnt, COALESCE(SUM(total_paid), 0) as total
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed')
            AND total_paid > %(threshold)s AND {where}
    """.format(where=where), {**params, "threshold": threshold}, as_dict=1)[0]  # noqa: UP032
    kpis["high_value_orders"] = hv.cnt or 0
    kpis["high_value_total"] = hv.total or 0

    # Manual vs auto approvals
    manual_approvals = frappe.db.sql("""
        SELECT COUNT(DISTINCT a.reference_name)
        FROM `tabBuyback Audit Log` a
        INNER JOIN `tabBuyback Order` o
            ON a.reference_doctype = 'Buyback Order' AND a.reference_name = o.name
        WHERE a.action IN ('Manual Approval','Price Override','Grade Changed')
            AND a.creation BETWEEN %(from_date)s AND %(to_date_end)s
            AND {order_scope}
    """.format(order_scope=order_scope), scoped_date_params)[0][0] or 0  # noqa: UP032
    kpis["manual_approvals"] = manual_approvals

    total_approved = frappe.db.sql("""
        SELECT COUNT(*)
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed','Approved','Customer Approved','OTP Verified')
            AND {where}
    """.format(where=where), params)[0][0] or 0  # noqa: UP032
    kpis["auto_approvals"] = max(total_approved - manual_approvals, 0)

    # SLA breaches
    sla = frappe.db.sql("""
        SELECT COUNT(*) as breaches
        FROM `tabBuyback SLA Log`
        WHERE breached=1 AND {where}
    """.format(where=where), params, as_dict=1)[0]  # noqa: UP032
    kpis["sla_breaches"] = sla.breaches or 0

    # Suspicious: branches with high override rate
    suspicious_threshold = get_int_setting("dashboard_suspicious_override_threshold", 3)
    suspicious_branches = frappe.db.sql(f"""
        SELECT o.store, COUNT(DISTINCT a.name) as override_count,
            COUNT(DISTINCT o.name) as order_count
        FROM `tabBuyback Audit Log` a
        JOIN `tabBuyback Order` o ON o.name = a.reference_name
            AND a.reference_doctype = 'Buyback Order'
        WHERE a.action IN ('Price Override','Grade Changed')
            AND a.creation BETWEEN %(from_date)s AND %(to_date_end)s
            AND {order_scope}
        GROUP BY o.store
        HAVING override_count > %(suspicious_threshold)s
        ORDER BY override_count DESC LIMIT 10
    """, {
        **scoped_date_params,
        "suspicious_threshold": suspicious_threshold,
    }, as_dict=1)

    # Recent audit actions
    recent_audits = frappe.db.sql("""
        SELECT a.creation, a.action, a.reference_name as reference,
            a.reference_doctype as reference_type, a.owner as user, a.reason
        FROM `tabBuyback Audit Log` a
        INNER JOIN `tabBuyback Order` o
            ON a.reference_doctype = 'Buyback Order' AND a.reference_name = o.name
        WHERE a.creation BETWEEN %(from_date)s AND %(to_date_end)s
            AND {order_scope}
        ORDER BY a.creation DESC LIMIT 20
    """.format(order_scope=order_scope), scoped_date_params, as_dict=1)  # noqa: UP032

    # Mismatch anomaly trend (daily)
    mismatch_trend = frappe.db.sql("""
        SELECT DATE(creation) as date,
            ROUND(AVG(mismatch_percentage),1) as avg_mismatch,
            COUNT(*) as cnt
        FROM `tabBuyback Inspection`
        WHERE status='Completed'
            AND {inspection_where}
        GROUP BY DATE(creation) ORDER BY date
    """.format(inspection_where=inspection_where), inspection_params, as_dict=1)  # noqa: UP032

    return {
        "kpis": kpis,
        "suspicious_branches": suspicious_branches,
        "mismatch_trend": mismatch_trend,
        "recent_audits": recent_audits,
    }


# ═══════════════════════════════════════════════════════════════════
# OPERATIONS DASHBOARD (for ops team / branch lead)
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_operations_dashboard(from_date=None, to_date=None, store=None) -> dict:
    """Operations — Real-time pipeline counts and SLA status."""
    _check_dashboard_access()
    from_date = from_date or nowdate()
    to_date = to_date or nowdate()
    from_date, to_date = _validate_date_range(from_date, to_date)

    if store:
        assert_buyback_scope(store=store)
    where, params = _build_params(
        from_date, to_date, store=store, scope_prefix="operations_dashboard"
    )

    # ── SLA overview ──
    sla_row = frappe.db.sql("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN breached=1 THEN 1 ELSE 0 END) as breached
        FROM `tabBuyback SLA Log`
        WHERE {where}
    """.format(where=where), params, as_dict=1)[0]  # noqa: UP032

    sla_total = sla_row.total or 0
    sla_breaches_cnt = sla_row.breached or 0
    sla_on_time = sla_total - sla_breaches_cnt
    sla_compliance = round(sla_on_time / max(sla_total, 1) * 100, 1) if sla_total else 100.0

    kpis = {
        "sla_on_time": sla_on_time,
        "sla_warnings": 0,  # no warning flag tracked yet
        "sla_breaches": sla_breaches_cnt,
        "sla_compliance": sla_compliance,
    }

    # ── Inspection pipeline ──
    inspection_pipeline = frappe.db.sql("""
        SELECT status, COUNT(*) as count
        FROM `tabBuyback Inspection`
        WHERE {where}
        GROUP BY status ORDER BY count DESC
    """.format(where=where), params, as_dict=1)  # noqa: UP032

    # ── Exchange pipeline ──
    exchange_pipeline = frappe.db.sql("""
        SELECT status, COUNT(*) as count
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND settlement_type='Exchange'
            AND {where}
        GROUP BY status ORDER BY count DESC
    """.format(where=where), params, as_dict=1)  # noqa: UP032

    # ── Hourly volume ──
    hourly_volume = frappe.db.sql("""
        SELECT CONCAT(LPAD(HOUR(creation), 2, '0'), ':00') as hour,
            COUNT(*) as count
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND {where}
        GROUP BY HOUR(creation) ORDER BY HOUR(creation)
    """.format(where=where), params, as_dict=1)  # noqa: UP032

    return {
        "kpis": kpis,
        "inspection_pipeline": inspection_pipeline,
        "exchange_pipeline": exchange_pipeline,
        "hourly_volume": hourly_volume,
    }
