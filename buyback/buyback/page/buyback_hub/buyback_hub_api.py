"""Buyback Hub – Backend API for buyback & exchange dashboard."""

import frappe
from frappe.utils import flt, nowdate, get_first_day, cint, getdate

# Import scope-aware filter builder (H6)
try:
    from ch_erp15.ch_erp15.scope import intersect_filters
except ImportError:
    # Fallback if ch_erp15 not available (unrestricted mode)
    def intersect_filters(**kwargs):
        return {
            "company": kwargs.get("company"),
            "store": kwargs.get("store"),
            "allowed_stores": None,
            "allowed_warehouses": None,
        }


def _store_warehouses(store_names):
    """All warehouses belonging to the given CH Stores.

    Buyback Order.store is a *Warehouse* (Sellable / Buyback / Damaged / Demo),
    and each CH Store owns several of them under its `warehouse_group`. So a
    store selection expands to every warehouse in that store's group (plus its
    primary warehouse) to catch buyback receipts booked at any of them.
    """
    if not store_names:
        return []
    rows = frappe.get_all(
        "CH Store", filters={"name": ("in", list(store_names))},
        fields=["warehouse", "warehouse_group"],
    )
    whs = set()
    groups = set()
    for r in rows:
        if r.warehouse:
            whs.add(r.warehouse)
        if r.warehouse_group:
            groups.add(r.warehouse_group)
    if groups:
        for w in frappe.get_all(
            "Warehouse",
            filters={"parent_warehouse": ("in", list(groups)), "is_group": 0},
            pluck="name",
        ):
            whs.add(w)
    return sorted(whs)


def _build_filters(company=None, store=None, from_date=None, to_date=None, city=None, zone=None):
    # SECURITY (H6): Enforce user's company/store scope + hierarchy selection
    eff = intersect_filters(company=company, city=city, zone=zone, store=store)
    company = eff["company"]
    allowed_stores = eff["allowed_stores"]  # None = unrestricted, [] = blocked, [list] = restricted

    prm = {}
    co = ""
    st = ""
    if company:
        co = " AND bo.company = %(company)s"
        prm["company"] = company
    if allowed_stores is not None:
        # A City / Zone / Store selection (or a scoped user) → restrict to the
        # warehouses of those stores.
        whs = _store_warehouses(allowed_stores) if allowed_stores else []
        if not whs:
            st = " AND 1=0"
        else:
            st_in = "(" + ", ".join(frappe.db.escape(w) for w in whs) + ")"
            st = f" AND bo.store IN {st_in}"

    from_date = str(getdate(from_date)) if from_date else None
    to_date = str(getdate(to_date)) if to_date else None
    if from_date:
        prm["from_date"] = from_date
    if to_date:
        prm["to_date"] = to_date

    def date_col(col):
        if from_date and to_date:
            return f" AND {col} BETWEEN %(from_date)s AND %(to_date)s"
        if from_date:
            return f" AND {col} >= %(from_date)s"
        if to_date:
            return f" AND {col} <= %(to_date)s"
        return ""

    return {"prm": prm, "co": co, "st": st, "date_col": date_col}


@frappe.whitelist()
def get_buyback_hub_data(company=None, store=None, from_date=None, to_date=None, city=None, zone=None):
    """Buyback lifecycle dashboard: Assessment → OTP → Approval → Inspection → Payment → Closed."""
    f = _build_filters(company, store, from_date, to_date, city=city, zone=zone)
    prm = f["prm"]
    co = f["co"]
    st = f["st"]
    dc = f["date_col"]

    today = nowdate()
    first_day = get_first_day(today)
    prm["today"] = today
    prm["first_day"] = str(first_day)

    # ── Pipeline by status ──
    status_counts = frappe.db.sql(
        f"""SELECT bo.status, COUNT(*) AS cnt
            FROM `tabBuyback Order` bo
            WHERE bo.docstatus < 2 {co} {st} {dc('bo.creation')}
            GROUP BY bo.status""", prm, as_dict=True
    )
    sc = {r.status: cint(r.cnt) for r in status_counts}

    pipeline = [
        {"key": "draft",       "label": "Draft",           "count": sc.get("Draft", 0),
         "icon": "pencil",      "color": "#94a3b8",  "sub": "New orders"},
        {"key": "otp",         "label": "Awaiting OTP",    "count": sc.get("Awaiting OTP", 0),
         "icon": "mobile",      "color": "#f59e0b",  "sub": "Customer verification"},
        {"key": "cust_appr",   "label": "Customer Approval","count": sc.get("Awaiting Customer Approval", 0),
         "icon": "user",        "color": "#8b5cf6",  "sub": "Price confirmation"},
        {"key": "approved",    "label": "Approved",         "count": sc.get("Approved", 0),
         "icon": "check",       "color": "#3b82f6",  "sub": "Ready to process"},
        {"key": "paid",        "label": "Paid",             "count": sc.get("Paid", 0),
         "icon": "money",       "color": "#059669",  "sub": "Payment completed"},
        {"key": "closed",      "label": "Closed",           "count": sc.get("Closed", 0),
         "icon": "check-circle","color": "#10b981",  "sub": "Fully processed"},
        {"key": "rejected",    "label": "Rejected",         "count": sc.get("Rejected", 0),
         "icon": "times-circle","color": "#ef4444",  "sub": "Declined"},
    ]

    # ── KPIs ──
    total_orders = sum(sc.values())
    active_orders = sc.get("Draft", 0) + sc.get("Awaiting OTP", 0) + sc.get("Awaiting Customer Approval", 0) + sc.get("Approved", 0)

    today_orders = frappe.db.sql(
        f"""SELECT COUNT(*) FROM `tabBuyback Order` bo
            WHERE DATE(bo.creation) = %(today)s {co} {st}""", prm
    )[0][0]

    total_value = frappe.db.sql(
        f"""SELECT COALESCE(SUM(bo.final_price), 0) FROM `tabBuyback Order` bo
            WHERE bo.status IN ('Approved','Paid','Closed') {co} {st} {dc('bo.creation')}""", prm
    )[0][0]

    mtd_value = frappe.db.sql(
        f"""SELECT COALESCE(SUM(bo.final_price), 0) FROM `tabBuyback Order` bo
            WHERE bo.status IN ('Approved','Paid','Closed')
            AND bo.creation BETWEEN %(first_day)s AND %(today)s {co} {st}""", prm
    )[0][0]

    avg_order = flt(total_value) / max(total_orders - sc.get("Rejected", 0) - sc.get("Draft", 0), 1)

    approved_total = sc.get("Approved", 0) + sc.get("Paid", 0) + sc.get("Closed", 0)
    rejected_total = sc.get("Rejected", 0)
    decided_total = approved_total + rejected_total
    approval_rate = f"{approved_total*100//max(decided_total,1)}%" if decided_total else "N/A"
    rejection_rate = f"{rejected_total*100//max(decided_total,1)}%" if decided_total else "N/A"

    # Assessment & inspection counts
    assessment_count = frappe.db.sql(
        f"""SELECT COUNT(*) FROM `tabBuyback Assessment` ba
            WHERE 1=1 {co.replace('bo.','ba.')} {st.replace('bo.','ba.')} {dc('ba.creation').replace('bo.','ba.')}""", prm
    )[0][0]

    inspection_count = frappe.db.sql(
        f"""SELECT COUNT(*) FROM `tabBuyback Inspection` bi
            WHERE 1=1 {co.replace('bo.','bi.')} {st.replace('bo.','bi.')} {dc('bi.creation').replace('bo.','bi.')}""", prm
    )[0][0]

    kpis = [
        {"key": "today",        "label": "Orders Today",      "value": cint(today_orders),   "color": "#ea580c", "fmt": "number"},
        {"key": "active",       "label": "Active Orders",     "value": active_orders,         "color": "#f59e0b", "fmt": "number"},
        {"key": "total_value",  "label": "Total Value",       "value": flt(total_value),      "color": "#059669", "fmt": "currency"},
        {"key": "mtd",          "label": "MTD Value",          "value": flt(mtd_value),        "color": "#10b981", "fmt": "currency"},
        {"key": "avg_order",    "label": "Avg Order Value",   "value": avg_order,             "color": "#3b82f6", "fmt": "currency"},
        {"key": "assessments",  "label": "Assessments",       "value": cint(assessment_count),"color": "#6366f1", "fmt": "number"},
        {"key": "inspections",  "label": "Inspections",       "value": cint(inspection_count),"color": "#8b5cf6", "fmt": "number"},
        {"key": "total",        "label": "Total Orders",      "value": total_orders,          "color": "#0ea5e9", "fmt": "number"},
    ]

    # ── Detail tables ──
    recent_orders = frappe.db.sql(
        f"""SELECT bo.name, bo.customer, bo.customer_name, bo.item_name,
                   bo.status, bo.final_price AS buyback_value, bo.creation
            FROM `tabBuyback Order` bo
            WHERE bo.docstatus < 2 {co} {st} {dc('bo.creation')}
            ORDER BY bo.creation DESC LIMIT 50""", prm, as_dict=True
    )

    pending_action = frappe.db.sql(
        f"""SELECT bo.name, bo.customer, bo.customer_name, bo.status, bo.modified,
                   DATEDIFF(%(today)s, bo.modified) AS days_pending
            FROM `tabBuyback Order` bo
            WHERE bo.status IN ('Draft','Awaiting OTP','Awaiting Customer Approval','Approved')
            {co} {st}
            ORDER BY bo.modified ASC LIMIT 50""", prm, as_dict=True
    )

    recent_assessments = frappe.db.sql(
        f"""SELECT ba.name, ba.customer_name, ba.item_name,
                   ba.estimated_grade AS grade,
                   COALESCE(gm.grade_name, ba.estimated_grade) AS grade_name,
                   ba.estimated_price, ba.creation
            FROM `tabBuyback Assessment` ba
            LEFT JOIN `tabGrade Master` gm ON gm.name = ba.estimated_grade
            WHERE 1=1 {co.replace('bo.','ba.')} {st.replace('bo.','ba.')} {dc('ba.creation').replace('bo.','ba.')}
            ORDER BY ba.creation DESC LIMIT 30""", prm, as_dict=True
    )

    recent_inspections = frappe.db.sql(
        f"""SELECT bi.name, bi.buyback_assessment, bi.condition_grade,
                   COALESCE(gm.grade_name, bi.condition_grade) AS condition_grade_name,
                   bi.status, bi.creation
            FROM `tabBuyback Inspection` bi
            LEFT JOIN `tabGrade Master` gm ON gm.name = bi.condition_grade
            WHERE 1=1 {co.replace('bo.','bi.')} {st.replace('bo.','bi.')} {dc('bi.creation').replace('bo.','bi.')}
            ORDER BY bi.creation DESC LIMIT 30""", prm, as_dict=True
    )

    brand_summary = frappe.db.sql(
        f"""SELECT bo.brand,
                   COUNT(*) AS total,
                   SUM(CASE WHEN bo.status IN ('Approved','Paid','Closed') THEN 1 ELSE 0 END) AS approved,
                   SUM(CASE WHEN bo.status = 'Rejected' THEN 1 ELSE 0 END) AS rejected,
                   SUM(CASE WHEN bo.status IN ('Approved','Paid','Closed') THEN bo.final_price ELSE 0 END) AS total_value
            FROM `tabBuyback Order` bo
            WHERE bo.docstatus < 2 {co} {st} {dc('bo.creation')}
            GROUP BY bo.brand
            ORDER BY total DESC LIMIT 20""", prm, as_dict=True
    )

    # ── AI Insights ──
    ai_insights = []
    stuck_otp = sc.get("Awaiting OTP", 0)
    if stuck_otp > 5:
        ai_insights.append({
            "severity": "High", "title": f"{stuck_otp} Orders Stuck at OTP",
            "detail": "Multiple orders waiting for OTP verification. Customers may need assistance.",
            "action": "Follow up with customers for OTP completion or resend OTP."
        })
    if active_orders > 20:
        ai_insights.append({
            "severity": "Medium", "title": f"High Active Backlog ({active_orders} orders)",
            "detail": "Consider expediting assessment and approval processes.",
            "action": "Review pipeline for bottlenecks in assessment or approval stages."
        })
    if rejected_total > approved_total * 0.3 and decided_total > 5:
        ai_insights.append({
            "severity": "Medium", "title": f"High Rejection Rate ({rejection_rate})",
            "detail": "Over 30% of decided orders are rejected. Review criteria.",
            "action": "Analyze rejection reasons to improve initial screening."
        })
    if not ai_insights:
        ai_insights.append({
            "severity": "Low", "title": "Buyback Operations on Track",
            "detail": "No significant anomalies detected in buyback workflow.",
        })

    financial_control = {
        "total_buyback_value": flt(total_value),
        "approval_rate": approval_rate,
        "avg_order_value": avg_order,
        "rejection_rate": rejection_rate,
    }

    return {
        "pipeline": pipeline,
        "kpis": kpis,
        "recent_orders": recent_orders,
        "pending_action": pending_action,
        "recent_assessments": recent_assessments,
        "recent_inspections": recent_inspections,
        "brand_summary": brand_summary,
        "ai_insights": ai_insights,
        "financial_control": financial_control,
    }
