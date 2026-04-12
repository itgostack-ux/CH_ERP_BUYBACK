# Copyright (c) 2026, GoStack and contributors
# Dashboard API — Backend data for all persona dashboards.
# Rebuilt to reflect the unified buyback + exchange + assessment flow.
# B2 gap closure: all queries use parameterized %(key)s placeholders.

import frappe
from frappe import _
from frappe.utils import nowdate, add_months, add_days, date_diff, flt


def _date_params(from_date, to_date):
    """Return standard date parameters for queries."""
    return {"from_date": from_date, "to_date_end": f"{to_date} 23:59:59"}


def _build_params(from_date, to_date, col="creation", **kwargs):
    """Build a params dict and a list of SQL AND-clauses from optional filters.

    Returns (sql_conditions_str, params_dict).
    The date range clause is always included.
    Optional filters (company, store, brand, item_group) are added only when truthy.
    """
    params = _date_params(from_date, to_date)
    clauses = [f"{col} BETWEEN %(from_date)s AND %(to_date_end)s"]

    field_map = {
        "company": "company",
        "store": "store",
        "brand": "brand",
        "item_group": "item_group",
    }

    for key, db_field in field_map.items():
        value = kwargs.get(key)
        if value:
            params[key] = value
            clauses.append(f"{db_field} = %({key})s")

    return " AND ".join(clauses), params


def _check_dashboard_access():
    """Ensure caller has at least read access to Buyback Order."""
    if not frappe.has_permission("Buyback Order", "read"):
        frappe.throw(_("You do not have permission to view buyback dashboards"), frappe.PermissionError, title=_("API Error"))


# ═══════════════════════════════════════════════════════════════════
# STORE MANAGER DASHBOARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_store_dashboard(store=None, from_date=None, to_date=None) -> dict:
    """Store Manager — Branch-level performance with pending action counts."""
    _check_dashboard_access()
    from_date = from_date or nowdate()
    to_date = to_date or nowdate()
    if not store:
        frappe.throw(_("Store is required"), title=_("API Error"))

    where, params = _build_params(from_date, to_date, store=store)

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
# CATEGORY MANAGER DASHBOARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_category_dashboard(from_date=None, to_date=None, brand=None, item_group=None) -> dict:
    """Category Manager — Model/brand performance & mismatch hotspots."""
    _check_dashboard_access()
    from_date = from_date or add_months(nowdate(), -1)
    to_date = to_date or nowdate()

    where, params = _build_params(from_date, to_date, brand=brand, item_group=item_group)

    # Build a separate where clause for Buyback Order (no item_group column)
    order_where, order_params = _build_params(from_date, to_date, brand=brand)

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
    mm_clauses = ["o.creation BETWEEN %(from_date)s AND %(to_date_end)s"]
    if brand:
        mm_clauses.append("o.brand = %(brand)s")
    # Note: item_group does not exist on tabBuyback Order, so not filtered here
    mm_where = " AND ".join(mm_clauses)

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
    """.format(where=mm_where), params, as_dict=1)  # noqa: UP032

    # Grade mix (for pie chart) — uses order_where (no item_group on Buyback Order)
    grade_data = frappe.db.sql("""
        SELECT condition_grade as grade, COUNT(*) as qty
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND {where} AND condition_grade IS NOT NULL
        GROUP BY condition_grade ORDER BY qty DESC
    """.format(where=order_where), order_params, as_dict=1)  # noqa: UP032

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

    where, params = _build_params(from_date, to_date, company=company)

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

    # High-value payouts (> 50000)
    high_value = frappe.db.sql("""
        SELECT name, store, customer_name, total_paid, customer_payout_mode, customer_payout_updated_at as payment_date
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed')
            AND total_paid > 50000 AND {where}
        ORDER BY total_paid DESC LIMIT 20
    """.format(where=where), params, as_dict=1)  # noqa: UP032

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

    where, params = _build_params(from_date, to_date, company=company)
    date_params = _date_params(from_date, to_date)
    kpis = {}

    # OTP failures
    otp_row = frappe.db.sql("""
        SELECT
            COUNT(*) as total_otp,
            SUM(CASE WHEN status IN ('Failed','Expired') THEN 1 ELSE 0 END) as failures
        FROM `tabCH OTP Log`
        WHERE creation BETWEEN %(from_date)s AND %(to_date_end)s
    """, date_params, as_dict=1)[0]
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
                AND creation BETWEEN %(from_date)s AND %(to_date_end)s
            GROUP BY imei_serial HAVING COUNT(*) > 1
        ) dup
    """, date_params)[0][0] or 0

    # Manager overrides
    kpis["manager_overrides"] = frappe.db.count("Buyback Audit Log", {
        "action": ["in", ["Price Override", "Grade Changed"]],
        "creation": ("between", [from_date, f"{to_date} 23:59:59"]),
    })

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
        SELECT COUNT(DISTINCT reference_name)
        FROM `tabBuyback Audit Log`
        WHERE action IN ('Manual Approval','Price Override','Grade Changed')
            AND creation BETWEEN %(from_date)s AND %(to_date_end)s
    """, date_params)[0][0] or 0
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
    suspicious_branches = frappe.db.sql("""
        SELECT o.store, COUNT(DISTINCT a.name) as override_count,
            COUNT(DISTINCT o.name) as order_count
        FROM `tabBuyback Audit Log` a
        JOIN `tabBuyback Order` o ON o.name = a.reference_name
            AND a.reference_doctype = 'Buyback Order'
        WHERE a.action IN ('Price Override','Grade Changed')
            AND a.creation BETWEEN %(from_date)s AND %(to_date_end)s
        GROUP BY o.store
        HAVING override_count > 3
        ORDER BY override_count DESC LIMIT 10
    """, date_params, as_dict=1)

    # Recent audit actions
    recent_audits = frappe.db.sql("""
        SELECT creation, action, reference_name as reference,
            reference_doctype as reference_type, owner as user, reason
        FROM `tabBuyback Audit Log`
        WHERE creation BETWEEN %(from_date)s AND %(to_date_end)s
        ORDER BY creation DESC LIMIT 20
    """, date_params, as_dict=1)

    # Mismatch anomaly trend (daily)
    mismatch_trend = frappe.db.sql("""
        SELECT DATE(creation) as date,
            ROUND(AVG(mismatch_percentage),1) as avg_mismatch,
            COUNT(*) as cnt
        FROM `tabBuyback Inspection`
        WHERE status='Completed'
            AND creation BETWEEN %(from_date)s AND %(to_date_end)s
        GROUP BY DATE(creation) ORDER BY date
    """, date_params, as_dict=1)

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

    where, params = _build_params(from_date, to_date, store=store)

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
