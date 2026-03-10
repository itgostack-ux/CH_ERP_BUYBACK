# Copyright (c) 2026, GoStack and contributors
# R1 — Unified Buyback Funnel
# Assessment → Inspection → Approval → Settlement → Closure with source split.

import frappe
from frappe import _
from buyback.buyback.report.report_utils import date_condition, standard_conditions


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    chart = get_chart(data)
    summary = get_summary(data)
    return columns, data, None, chart, summary


def get_columns():
    return [
        {"fieldname": "stage", "label": _("Funnel Stage"), "fieldtype": "Data", "width": 260},
        {"fieldname": "total", "label": _("Total"), "fieldtype": "Int", "width": 90},
        {"fieldname": "app_count", "label": _("App Diagnosis"), "fieldtype": "Int", "width": 120},
        {"fieldname": "manual_count", "label": _("Store Manual"), "fieldtype": "Int", "width": 120},
        {"fieldname": "value", "label": _("Value (₹)"), "fieldtype": "Currency", "width": 140},
        {"fieldname": "conversion_pct", "label": _("Conversion %"), "fieldtype": "Percent", "width": 110},
        {"fieldname": "drop_off", "label": _("Drop-off"), "fieldtype": "Int", "width": 90},
    ]


def get_data(filters):
    dc = date_condition("creation", filters)
    sc = standard_conditions(filters)

    def _q(sql):
        return frappe.db.sql(sql, as_dict=1)[0]

    # Stage 1: Assessments
    q = _q(f"""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN source='App Diagnosis' THEN 1 ELSE 0 END) as app_count,
               SUM(CASE WHEN IFNULL(source,'Store Manual')='Store Manual' THEN 1 ELSE 0 END) as manual_count,
               COALESCE(SUM(IFNULL(quoted_price, estimated_price)),0) as value
        FROM `tabBuyback Assessment` WHERE {dc} {sc}
    """)

    # Stage 2: Inspections Completed
    insp = _q(f"""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN a.source='App Diagnosis' THEN 1 ELSE 0 END) as app_count,
               SUM(CASE WHEN IFNULL(a.source,'Store Manual')='Store Manual' THEN 1 ELSE 0 END) as manual_count
        FROM `tabBuyback Inspection` i
        LEFT JOIN `tabBuyback Assessment` a ON a.name = i.buyback_assessment
        WHERE i.status='Completed' AND {dc.replace('creation','i.creation')} {sc.replace(' AND ',' AND i.',1) if sc else ''}
    """)

    # Stage 3: Orders Created
    o_dc = dc.replace("creation", "o.creation")
    o_sc_raw = standard_conditions(filters, alias="o.")
    o = _q(f"""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN a.source='App Diagnosis' THEN 1 ELSE 0 END) as app_count,
               SUM(CASE WHEN IFNULL(a.source,'Store Manual')='Store Manual' THEN 1 ELSE 0 END) as manual_count,
               COALESCE(SUM(o.final_price),0) as value
        FROM `tabBuyback Order` o
        LEFT JOIN `tabBuyback Assessment` a ON a.name = o.buyback_assessment
        WHERE o.docstatus<2 AND {o_dc} {o_sc_raw}
    """)

    # Stage 4: Customer Approved
    ca = _q(f"""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN a.source='App Diagnosis' THEN 1 ELSE 0 END) as app_count,
               SUM(CASE WHEN IFNULL(a.source,'Store Manual')='Store Manual' THEN 1 ELSE 0 END) as manual_count,
               COALESCE(SUM(o.final_price),0) as value
        FROM `tabBuyback Order` o
        LEFT JOIN `tabBuyback Assessment` a ON a.name = o.buyback_assessment
        WHERE o.docstatus<2 AND o.customer_approved=1 AND {o_dc} {o_sc_raw}
    """)

    # Stage 5: Settled (Paid/Closed)
    st = _q(f"""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN a.source='App Diagnosis' THEN 1 ELSE 0 END) as app_count,
               SUM(CASE WHEN IFNULL(a.source,'Store Manual')='Store Manual' THEN 1 ELSE 0 END) as manual_count,
               COALESCE(SUM(o.total_paid),0) as value
        FROM `tabBuyback Order` o
        LEFT JOIN `tabBuyback Assessment` a ON a.name = o.buyback_assessment
        WHERE o.docstatus<2 AND o.status IN ('Paid','Closed') AND {o_dc} {o_sc_raw}
    """)

    # Stage 6: Closed
    cl = _q(f"""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN a.source='App Diagnosis' THEN 1 ELSE 0 END) as app_count,
               SUM(CASE WHEN IFNULL(a.source,'Store Manual')='Store Manual' THEN 1 ELSE 0 END) as manual_count,
               COALESCE(SUM(o.total_paid),0) as value
        FROM `tabBuyback Order` o
        LEFT JOIN `tabBuyback Assessment` a ON a.name = o.buyback_assessment
        WHERE o.docstatus<2 AND o.status='Closed' AND {o_dc} {o_sc_raw}
    """)

    stages = [
        {"stage": "1. Assessments Created",    **q},
        {"stage": "2. Inspections Completed",  **insp, "value": 0},
        {"stage": "3. Orders Created",         **o},
        {"stage": "4. Customer Approved",      **ca},
        {"stage": "5. Settled (Paid)",         **st},
        {"stage": "6. Closed (Stock In)",      **cl},
    ]

    for i, row in enumerate(stages):
        row.setdefault("value", 0)
        if i == 0:
            row["conversion_pct"] = 100.0
            row["drop_off"] = 0
        else:
            prev = stages[i-1]["total"] or 1
            row["conversion_pct"] = round((row["total"] or 0) / prev * 100, 1)
            row["drop_off"] = (stages[i-1]["total"] or 0) - (row["total"] or 0)
    return stages


def get_chart(data):
    main = [d for d in data if not d["stage"].startswith("   ")]
    return {
        "data": {
            "labels": [d["stage"] for d in main],
            "datasets": [
                {"name": _("App Diagnosis"), "values": [d.get("app_count",0) for d in main]},
                {"name": _("Store Manual"),  "values": [d.get("manual_count",0) for d in main]},
            ],
        },
        "type": "bar",
        "colors": ["#5e64ff", "#ff5858"],
        "barOptions": {"stacked": 1},
    }


def get_summary(data):
    if not data:
        return []
    total_assessments = data[0].get("total", 0)
    closed = data[-1].get("total", 0) if data else 0
    pct = round(closed / total_assessments * 100, 1) if total_assessments else 0
    return [
        {"value": total_assessments, "label": _("Total Assessments"), "datatype": "Int"},
        {"value": closed, "label": _("Closed"), "datatype": "Int", "indicator": "green"},
        {"value": pct, "label": _("End-to-End %"), "datatype": "Percent", "indicator": "blue"},
    ]
