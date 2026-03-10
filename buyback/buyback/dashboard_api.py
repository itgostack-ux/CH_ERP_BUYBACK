# Copyright (c) 2026, GoStack and contributors
# Dashboard API — Backend data for all persona dashboards.
# Rebuilt to reflect the unified buyback + exchange + assessment flow.

import frappe
from frappe import _
from frappe.utils import nowdate, add_months, flt


def _dc(from_date, to_date, col="creation"):
    """Build date condition."""
    return f"{col} BETWEEN '{from_date}' AND '{to_date} 23:59:59'"


def _esc_cond(field, value):
    return f"AND {field} = {frappe.db.escape(value)}" if value else ""


# ═══════════════════════════════════════════════════════════════════
# CEO / OWNER DASHBOARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_ceo_dashboard(from_date=None, to_date=None, company=None):
    """CEO-level KPIs: volume, revenue, conversion, source mix, SLA, settlement mix."""
    from_date = from_date or add_months(nowdate(), -1)
    to_date = to_date or nowdate()
    cc = _esc_cond("company", company)
    dc = _dc(from_date, to_date)

    kpis = {}

    # ── Assessment metrics with source split ──
    a_row = frappe.db.sql(f"""
        SELECT
            COUNT(*) as total_assessments,
            SUM(CASE WHEN source='App Diagnosis' THEN 1 ELSE 0 END) as app_assessments,
            SUM(CASE WHEN IFNULL(source,'Store Manual')='Store Manual' THEN 1 ELSE 0 END) as manual_assessments
        FROM `tabBuyback Assessment` WHERE {dc} {cc}
    """, as_dict=1)[0]
    kpis["total_assessments"] = a_row.total_assessments or 0
    kpis["app_assessments"] = a_row.app_assessments or 0
    kpis["manual_assessments"] = a_row.manual_assessments or 0
    kpis["app_pct"] = round((a_row.app_assessments or 0) / max(a_row.total_assessments, 1) * 100, 1)

    # ── Order metrics with settlement split ──
    o_row = frappe.db.sql(f"""
        SELECT
            COUNT(*) as total_orders,
            SUM(CASE WHEN status IN ('Paid','Closed') THEN 1 ELSE 0 END) as settled,
            COALESCE(SUM(CASE WHEN status IN ('Paid','Closed') THEN total_paid ELSE 0 END), 0) as total_payout,
            SUM(CASE WHEN status IN ('Rejected','Cancelled') THEN 1 ELSE 0 END) as rejected,
            SUM(CASE WHEN customer_approved=1 THEN 1 ELSE 0 END) as customer_approved,
            SUM(CASE WHEN settlement_type='Exchange' AND status IN ('Paid','Closed') THEN 1 ELSE 0 END) as exchange_settled,
            SUM(CASE WHEN IFNULL(settlement_type,'Buyback')='Buyback' AND status IN ('Paid','Closed') THEN 1 ELSE 0 END) as buyback_settled,
            COALESCE(SUM(CASE WHEN settlement_type='Exchange' THEN IFNULL(exchange_discount,0) ELSE 0 END),0) as exchange_adj_value,
            ROUND(AVG(ABS(IFNULL(price_variance_pct,0))),1) as avg_variance_pct
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND {dc} {cc}
    """, as_dict=1)[0]

    kpis["total_orders"] = o_row.total_orders or 0
    kpis["settled"] = o_row.settled or 0
    kpis["total_payout"] = o_row.total_payout or 0
    kpis["rejected"] = o_row.rejected or 0
    kpis["customer_approved"] = o_row.customer_approved or 0
    kpis["conversion_rate"] = round((o_row.settled or 0) / max(kpis["total_assessments"], 1) * 100, 1)
    kpis["rejection_rate"] = round((o_row.rejected or 0) / max(o_row.total_orders, 1) * 100, 1)
    kpis["avg_order_value"] = round((o_row.total_payout or 0) / max(o_row.settled, 1), 0)
    kpis["avg_variance_pct"] = o_row.avg_variance_pct or 0
    kpis["buyback_settled"] = o_row.buyback_settled or 0
    kpis["exchange_settled"] = o_row.exchange_settled or 0
    kpis["exchange_adj_value"] = o_row.exchange_adj_value or 0

    settled_total = (o_row.buyback_settled or 0) + (o_row.exchange_settled or 0)
    kpis["buyback_pct"] = round((o_row.buyback_settled or 0) / max(settled_total, 1) * 100, 1)
    kpis["exchange_pct"] = round((o_row.exchange_settled or 0) / max(settled_total, 1) * 100, 1)

    # ── Customer approval rate ──
    inspected = frappe.db.count("Buyback Inspection", {
        "status": "Completed", "creation": ("between", [from_date, f"{to_date} 23:59:59"]),
    })
    kpis["customer_approval_rate"] = round(
        (o_row.customer_approved or 0) / max(inspected, 1) * 100, 1)

    # ── SLA compliance ──
    sla_row = frappe.db.sql(f"""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN breached=1 THEN 1 ELSE 0 END) as breached
        FROM `tabBuyback SLA Log`
        WHERE {dc} {cc}
    """, as_dict=1)[0]
    kpis["sla_compliance_pct"] = round(
        (1 - (sla_row.breached or 0) / max(sla_row.total, 1)) * 100, 1)

    # ── Mismatch avg ──
    mm_row = frappe.db.sql(f"""
        SELECT ROUND(AVG(IFNULL(mismatch_percentage,0)),1) as avg_mm
        FROM `tabBuyback Inspection`
        WHERE status='Completed' AND {dc}
    """, as_dict=1)[0]
    kpis["avg_mismatch_pct"] = mm_row.avg_mm or 0

    # ── Charts: daily funnel trend ──
    daily_trend = frappe.db.sql(f"""
        SELECT DATE(creation) as date,
            COUNT(*) as orders,
            COALESCE(SUM(CASE WHEN status IN ('Paid','Closed') THEN total_paid ELSE 0 END), 0) as payout
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND {dc} {cc}
        GROUP BY DATE(creation) ORDER BY date
    """, as_dict=1)

    # ── Source mix donut ──
    source_mix = [
        {"label": "App Diagnosis", "value": kpis["app_assessments"]},
        {"label": "Store Manual", "value": kpis["manual_assessments"]},
    ]

    # ── Settlement mix donut ──
    settlement_mix = [
        {"label": "Buyback", "value": kpis["buyback_settled"]},
        {"label": "Exchange", "value": kpis["exchange_settled"]},
    ]

    # ── Top 5 branches ──
    top_branches = frappe.db.sql(f"""
        SELECT store, COUNT(*) as orders,
            COALESCE(SUM(CASE WHEN status IN ('Paid','Closed') THEN total_paid ELSE 0 END), 0) as payout
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND {dc} {cc}
        GROUP BY store ORDER BY payout DESC LIMIT 5
    """, as_dict=1)

    # ── Top 5 models ──
    top_models = frappe.db.sql(f"""
        SELECT item, COUNT(*) as qty, COALESCE(SUM(final_price), 0) as value
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed') AND {dc} {cc}
        GROUP BY item ORDER BY qty DESC LIMIT 5
    """, as_dict=1)

    return {
        "kpis": kpis,
        "daily_trend": daily_trend,
        "source_mix": source_mix,
        "settlement_mix": settlement_mix,
        "top_branches": top_branches,
        "top_models": top_models,
    }


# ═══════════════════════════════════════════════════════════════════
# STORE MANAGER DASHBOARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_store_dashboard(store=None, from_date=None, to_date=None):
    """Store Manager — Branch-level performance with pending action counts."""
    from_date = from_date or nowdate()
    to_date = to_date or nowdate()
    if not store:
        frappe.throw(_("Store is required"))

    se = frappe.db.escape(store)
    dc = _dc(from_date, to_date)

    # KPIs
    o_row = frappe.db.sql(f"""
        SELECT
            COUNT(*) as total_orders,
            SUM(CASE WHEN status IN ('Paid','Closed') THEN 1 ELSE 0 END) as settled,
            COALESCE(SUM(CASE WHEN status IN ('Paid','Closed') THEN total_paid ELSE 0 END), 0) as total_payout,
            SUM(CASE WHEN status IN ('Draft','Awaiting Approval','Awaiting OTP','Awaiting Customer Approval') THEN 1 ELSE 0 END) as pending
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND store={se} AND {dc}
    """, as_dict=1)[0]

    # Source mix for this store
    src = frappe.db.sql(f"""
        SELECT
            SUM(CASE WHEN source='App Diagnosis' THEN 1 ELSE 0 END) as app_cnt,
            COUNT(*) as total
        FROM `tabBuyback Assessment`
        WHERE store={se} AND {dc}
    """, as_dict=1)[0]

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

    # Top models
    top_models = frappe.db.sql(f"""
        SELECT item, COUNT(*) as qty
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND store={se} AND {dc}
        GROUP BY item ORDER BY qty DESC LIMIT 5
    """, as_dict=1)

    return {
        "kpis": {
            "total_orders": o_row.total_orders or 0,
            "settled": o_row.settled or 0,
            "total_payout": o_row.total_payout or 0,
            "pending": o_row.pending or 0,
            "app_quote_pct": round((src.app_cnt or 0) / max(src.total, 1) * 100, 1),
            "pending_inspection": pending_inspection,
            "pending_approval": pending_approval,
            "pending_settlement": pending_settlement,
            "sla_breaches": sla_breaches,
        },
        "top_models": top_models,
    }


# ═══════════════════════════════════════════════════════════════════
# CATEGORY MANAGER DASHBOARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_category_dashboard(from_date=None, to_date=None, brand=None, item_group=None):
    """Category Manager — Model/brand performance & mismatch hotspots."""
    from_date = from_date or add_months(nowdate(), -1)
    to_date = to_date or nowdate()
    bc = _esc_cond("brand", brand)
    igc = _esc_cond("item_group", item_group)
    dc = _dc(from_date, to_date)

    # Top categories
    top_cats = frappe.db.sql(f"""
        SELECT item_group, COUNT(*) as cnt, COALESCE(SUM(IFNULL(quoted_price, estimated_price)),0) as value
        FROM `tabBuyback Assessment` WHERE {dc} {bc} {igc}
        GROUP BY item_group ORDER BY cnt DESC LIMIT 10
    """, as_dict=1)

    # Top brands
    top_brands = frappe.db.sql(f"""
        SELECT brand, COUNT(*) as cnt, COALESCE(SUM(IFNULL(quoted_price, estimated_price)),0) as value
        FROM `tabBuyback Assessment` WHERE {dc} {bc} {igc}
        GROUP BY brand ORDER BY cnt DESC LIMIT 10
    """, as_dict=1)

    # Model-wise inflow
    model_inflow = frappe.db.sql(f"""
        SELECT item, brand, item_group, COUNT(*) as cnt,
            ROUND(AVG(IFNULL(quoted_price, estimated_price)),0) as avg_price,
            ROUND(SUM(CASE WHEN source='App Diagnosis' THEN 1 ELSE 0 END) / COUNT(*) * 100, 1) as app_pct
        FROM `tabBuyback Assessment` WHERE {dc} {bc} {igc}
        GROUP BY item, brand, item_group ORDER BY cnt DESC LIMIT 20
    """, as_dict=1)

    # Mismatch hotspots (models with highest mismatch)
    obc = _esc_cond("o.brand", brand)
    oigc = _esc_cond("o.item_group", item_group)
    mismatch_hotspots = frappe.db.sql(f"""
        SELECT o.item, o.brand,
            ROUND(AVG(i.mismatch_percentage),1) as avg_mismatch,
            ROUND(AVG(ABS(IFNULL(o.price_variance_pct,0))),1) as avg_price_var,
            COUNT(*) as cnt
        FROM `tabBuyback Order` o
        JOIN `tabBuyback Inspection` i ON i.name = o.buyback_inspection
        WHERE o.docstatus < 2 AND {_dc(from_date, to_date, 'o.creation')} {obc} {oigc}
            AND i.mismatch_percentage > 0
        GROUP BY o.item, o.brand
        ORDER BY avg_mismatch DESC
        LIMIT 10
    """, as_dict=1)

    # Grade mix
    grade_mix = frappe.db.sql(f"""
        SELECT condition_grade, COUNT(*) as cnt
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND {dc} {bc} {igc} AND condition_grade IS NOT NULL
        GROUP BY condition_grade ORDER BY cnt DESC
    """, as_dict=1)

    # Settlement mix by brand
    settlement_by_brand = frappe.db.sql(f"""
        SELECT brand,
            SUM(CASE WHEN IFNULL(settlement_type,'Buyback')='Buyback' THEN 1 ELSE 0 END) as buyback,
            SUM(CASE WHEN settlement_type='Exchange' THEN 1 ELSE 0 END) as exchange
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed') AND {dc} {bc} {igc}
        GROUP BY brand ORDER BY buyback + exchange DESC LIMIT 10
    """, as_dict=1)

    return {
        "top_categories": top_cats,
        "top_brands": top_brands,
        "model_inflow": model_inflow,
        "mismatch_hotspots": mismatch_hotspots,
        "grade_mix": grade_mix,
        "settlement_by_brand": settlement_by_brand,
    }


# ═══════════════════════════════════════════════════════════════════
# FINANCE DASHBOARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_finance_dashboard(from_date=None, to_date=None, company=None):
    """Finance — Payouts, pending settlements, exchange adjustments."""
    from_date = from_date or add_months(nowdate(), -1)
    to_date = to_date or nowdate()
    cc = _esc_cond("company", company)
    dc = _dc(from_date, to_date)

    # Payout KPIs
    p_row = frappe.db.sql(f"""
        SELECT
            COUNT(*) as total_paid,
            COALESCE(SUM(total_paid), 0) as total_amount,
            SUM(CASE WHEN payment_mode='Cash' THEN 1 ELSE 0 END) as cash_count,
            COALESCE(SUM(CASE WHEN payment_mode='Cash' THEN total_paid ELSE 0 END), 0) as cash_amount,
            SUM(CASE WHEN payment_mode='Bank Transfer' THEN 1 ELSE 0 END) as bank_count,
            SUM(CASE WHEN payment_mode='UPI' THEN 1 ELSE 0 END) as upi_count
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed') AND {dc} {cc}
    """, as_dict=1)[0]

    # Pending payouts
    pending = frappe.db.sql(f"""
        SELECT COUNT(*) as cnt, COALESCE(SUM(final_price), 0) as amount
        FROM `tabBuyback Order`
        WHERE docstatus < 2
            AND status IN ('Approved','Customer Approved','OTP Verified')
            AND (total_paid IS NULL OR total_paid = 0)
            AND {dc} {cc}
    """, as_dict=1)[0]

    # Exchange adjustments
    ex = frappe.db.sql(f"""
        SELECT COUNT(*) as cnt,
            COALESCE(SUM(exchange_discount), 0) as adj_value,
            COALESCE(SUM(balance_to_pay), 0) as balance_due
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND settlement_type='Exchange'
            AND status IN ('Paid','Closed') AND {dc} {cc}
    """, as_dict=1)[0]

    # Branch-wise payout
    branch_payout = frappe.db.sql(f"""
        SELECT store, COALESCE(SUM(total_paid), 0) as amount, COUNT(*) as cnt
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed') AND {dc} {cc}
        GROUP BY store ORDER BY amount DESC LIMIT 10
    """, as_dict=1)

    # Payment mode mix
    mode_mix = frappe.db.sql(f"""
        SELECT IFNULL(payment_mode,'Unknown') as mode, COUNT(*) as cnt,
            COALESCE(SUM(total_paid), 0) as amount
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed') AND {dc} {cc}
        GROUP BY payment_mode ORDER BY cnt DESC
    """, as_dict=1)

    # High-value payouts (> 50000)
    high_value = frappe.db.sql(f"""
        SELECT name, store, customer_name, total_paid, payment_mode, payment_date
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed')
            AND total_paid > 50000 AND {dc} {cc}
        ORDER BY total_paid DESC LIMIT 20
    """, as_dict=1)

    return {
        "kpis": {
            "total_paid_count": p_row.total_paid or 0,
            "total_paid_amount": p_row.total_amount or 0,
            "cash_count": p_row.cash_count or 0,
            "cash_amount": p_row.cash_amount or 0,
            "bank_count": p_row.bank_count or 0,
            "upi_count": p_row.upi_count or 0,
            "pending_count": pending.cnt or 0,
            "pending_amount": pending.amount or 0,
            "exchange_count": ex.cnt or 0,
            "exchange_adj_value": ex.adj_value or 0,
            "exchange_balance_due": ex.balance_due or 0,
        },
        "branch_payout": branch_payout,
        "mode_mix": mode_mix,
        "high_value": high_value,
    }


# ═══════════════════════════════════════════════════════════════════
# COMPLIANCE / QA DASHBOARD
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_compliance_dashboard(from_date=None, to_date=None, company=None):
    """Compliance — Anomalies, OTP failures, mismatches, overrides."""
    from_date = from_date or add_months(nowdate(), -1)
    to_date = to_date or nowdate()
    cc = _esc_cond("company", company)
    dc = _dc(from_date, to_date)

    kpis = {}

    # OTP failures
    otp_row = frappe.db.sql(f"""
        SELECT
            COUNT(*) as total_otp,
            SUM(CASE WHEN status IN ('Failed','Expired') THEN 1 ELSE 0 END) as failures
        FROM `tabCH OTP Log`
        WHERE {dc}
    """, as_dict=1)[0]
    kpis["otp_total"] = otp_row.total_otp or 0
    kpis["otp_failures"] = otp_row.failures or 0
    kpis["otp_failure_rate"] = round(
        (otp_row.failures or 0) / max(otp_row.total_otp, 1) * 100, 1)

    # Missing approvals (paid without customer_approved)
    kpis["paid_without_approval"] = frappe.db.sql(f"""
        SELECT COUNT(*)
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status IN ('Paid','Closed')
            AND customer_approved != 1
            AND buyback_assessment IS NOT NULL AND buyback_assessment != ''
            AND {dc} {cc}
    """)[0][0] or 0

    # Duplicate IMEI
    kpis["duplicate_imeis"] = frappe.db.sql(f"""
        SELECT COUNT(*) FROM (
            SELECT imei_serial FROM `tabBuyback Assessment`
            WHERE imei_serial IS NOT NULL AND imei_serial != '' AND {dc}
            GROUP BY imei_serial HAVING COUNT(*) > 1
        ) dup
    """)[0][0] or 0

    # Overrides count
    kpis["overrides_count"] = frappe.db.count("Buyback Audit Log", {
        "action": ["in", ["Price Override", "Grade Changed"]],
        "creation": ("between", [from_date, f"{to_date} 23:59:59"]),
    })

    # High-mismatch inspections (>50%)
    kpis["high_mismatch_count"] = frappe.db.sql(f"""
        SELECT COUNT(*)
        FROM `tabBuyback Inspection`
        WHERE status='Completed' AND mismatch_percentage > 50 AND {dc}
    """)[0][0] or 0

    # SLA breaches
    sla = frappe.db.sql(f"""
        SELECT COUNT(*) as breaches
        FROM `tabBuyback SLA Log`
        WHERE breached=1 AND {dc} {cc}
    """, as_dict=1)[0]
    kpis["sla_breaches"] = sla.breaches or 0

    # Suspicious: branches with high override rate
    suspicious_branches = frappe.db.sql(f"""
        SELECT o.store, COUNT(DISTINCT a.name) as override_count,
            COUNT(DISTINCT o.name) as order_count
        FROM `tabBuyback Audit Log` a
        JOIN `tabBuyback Order` o ON o.name = a.reference_name
            AND a.reference_doctype = 'Buyback Order'
        WHERE a.action IN ('Price Override','Grade Changed')
            AND {_dc(from_date, to_date, 'a.creation')}
        GROUP BY o.store
        HAVING override_count > 3
        ORDER BY override_count DESC LIMIT 10
    """, as_dict=1)

    # Mismatch anomaly trend (daily)
    mismatch_trend = frappe.db.sql(f"""
        SELECT DATE(creation) as date,
            ROUND(AVG(mismatch_percentage),1) as avg_mismatch,
            COUNT(*) as cnt
        FROM `tabBuyback Inspection`
        WHERE status='Completed' AND {dc}
        GROUP BY DATE(creation) ORDER BY date
    """, as_dict=1)

    return {
        "kpis": kpis,
        "suspicious_branches": suspicious_branches,
        "mismatch_trend": mismatch_trend,
    }


# ═══════════════════════════════════════════════════════════════════
# OPERATIONS DASHBOARD (for ops team / branch lead)
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_operations_dashboard(from_date=None, to_date=None, store=None):
    """Operations — Real-time pipeline counts and SLA status."""
    from_date = from_date or nowdate()
    to_date = to_date or nowdate()
    sc = _esc_cond("store", store)
    dc = _dc(from_date, to_date)

    # Pipeline counts by status
    pipeline = frappe.db.sql(f"""
        SELECT
            status,
            COUNT(*) as cnt,
            ROUND(AVG(TIMESTAMPDIFF(MINUTE, creation, NOW())),1) as avg_age_min
        FROM `tabBuyback Order`
        WHERE docstatus < 2 AND status NOT IN ('Closed','Cancelled','Rejected')
            AND {dc} {sc}
        GROUP BY status
        ORDER BY FIELD(status,
            'Draft','Awaiting Approval','Approved',
            'Awaiting Customer Approval','Customer Approved',
            'Awaiting OTP','OTP Verified','Paid')
    """, as_dict=1)

    # Assessments awaiting inspection
    a_sc = sc.replace("store", "a.store") if sc else ""
    pending_insp = frappe.db.sql(f"""
        SELECT COUNT(*) as cnt
        FROM `tabBuyback Assessment` a
        WHERE a.status = 'Submitted'
            AND NOT EXISTS (
                SELECT 1 FROM `tabBuyback Inspection` i
                WHERE i.buyback_assessment = a.name AND i.status != 'Cancelled'
            )
            AND {_dc(from_date, to_date, 'a.creation')} {a_sc}
    """, as_dict=1)[0]

    # SLA breaches today
    sla_breaches = frappe.db.sql(f"""
        SELECT sla_stage, COUNT(*) as cnt
        FROM `tabBuyback SLA Log`
        WHERE breached=1 AND {dc} {sc}
        GROUP BY sla_stage
    """, as_dict=1)

    return {
        "pipeline": pipeline,
        "pending_inspection": pending_insp.cnt or 0,
        "sla_breaches": sla_breaches,
    }
