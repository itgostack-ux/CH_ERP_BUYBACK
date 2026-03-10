# Copyright (c) 2026, GoStack and contributors
# Store Scorecard — Composite performance score per branch.
# Includes app source %, mismatch %, SLA compliance, conversion.

import frappe
from frappe import _
from frappe.utils import flt
from buyback.buyback.report.report_utils import date_condition, standard_conditions


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    chart = get_chart(data)
    return columns, data, None, chart


def get_columns():
    return [
        {"fieldname": "store", "label": _("Branch"), "fieldtype": "Link", "options": "Warehouse", "width": 180},
        {"fieldname": "total_assessments", "label": _("Assessments"), "fieldtype": "Int", "width": 100},
        {"fieldname": "app_pct", "label": _("App Source %"), "fieldtype": "Percent", "width": 110},
        {"fieldname": "settled_count", "label": _("Settled"), "fieldtype": "Int", "width": 80},
        {"fieldname": "settled_value", "label": _("Settled ₹"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "conversion_pct", "label": _("Conversion %"), "fieldtype": "Percent", "width": 110},
        {"fieldname": "avg_mismatch_pct", "label": _("Mismatch %"), "fieldtype": "Percent", "width": 110},
        {"fieldname": "sla_compliance_pct", "label": _("SLA %"), "fieldtype": "Percent", "width": 100},
        {"fieldname": "composite_score", "label": _("Score"), "fieldtype": "Float", "width": 80, "precision": 1},
    ]


def get_data(filters):
    dc = date_condition("creation", filters)
    sc = standard_conditions(filters)

    assessment_map = {}
    for r in frappe.db.sql(f"""
        SELECT store, COUNT(*) as cnt,
               SUM(CASE WHEN source='App Diagnosis' THEN 1 ELSE 0 END) as app_cnt
        FROM `tabBuyback Assessment` WHERE {dc} {sc} AND store IS NOT NULL GROUP BY store
    """, as_dict=1):
        assessment_map[r.store] = r

    order_map = {}
    odc = dc.replace("creation", "o.creation")
    osc = standard_conditions(filters, alias="o.")
    for r in frappe.db.sql(f"""
        SELECT o.store,
               SUM(CASE WHEN o.status IN ('Paid','Closed') THEN 1 ELSE 0 END) as settled,
               COALESCE(SUM(CASE WHEN o.status IN ('Paid','Closed') THEN o.total_paid ELSE 0 END),0) as value
        FROM `tabBuyback Order` o
        WHERE o.docstatus<2 AND {odc} {osc} AND o.store IS NOT NULL GROUP BY o.store
    """, as_dict=1):
        order_map[r.store] = r

    insp_map = {}
    for r in frappe.db.sql(f"""
        SELECT store, ROUND(AVG(IFNULL(mismatch_percentage,0)),1) as avg_mm
        FROM `tabBuyback Inspection` WHERE status='Completed' AND {dc} AND store IS NOT NULL
        GROUP BY store
    """, as_dict=1):
        insp_map[r.store] = r.avg_mm

    sla_map = {}
    for r in frappe.db.sql(f"""
        SELECT store,
               SUM(CASE WHEN breached=1 THEN 1 ELSE 0 END) as breached,
               COUNT(*) as total
        FROM `tabBuyback SLA Log` WHERE {dc} AND store IS NOT NULL GROUP BY store
    """, as_dict=1):
        sla_map[r.store] = round((1 - r.breached / r.total) * 100, 1) if r.total else 100

    all_stores = set(list(assessment_map.keys()) + list(order_map.keys()))
    data = []
    for s in sorted(all_stores):
        am = assessment_map.get(s, {})
        om = order_map.get(s, {})
        assessments = am.get("cnt", 0) or 0
        app_cnt = am.get("app_cnt", 0) or 0
        settled = om.get("settled", 0) or 0
        value = om.get("value", 0) or 0
        mm = insp_map.get(s, 0)
        sla = sla_map.get(s, 100)
        conv = round(settled / assessments * 100, 1) if assessments else 0

        score = round(
            0.30 * conv + 0.25 * sla + 0.20 * (100 - mm)
            + 0.15 * min(app_cnt / max(assessments,1) * 100, 100)
            + 0.10 * min(settled,100), 1)

        data.append({
            "store": s, "total_assessments": assessments,
            "app_pct": round(app_cnt / assessments * 100, 1) if assessments else 0,
            "settled_count": settled, "settled_value": value,
            "conversion_pct": conv, "avg_mismatch_pct": mm,
            "sla_compliance_pct": sla, "composite_score": score,
        })
    data.sort(key=lambda x: x.get("composite_score",0), reverse=True)
    return data


def get_chart(data):
    if not data:
        return None
    top = data[:15]
    return {
        "data": {
            "labels": [d["store"] for d in top],
            "datasets": [{"name": _("Score"), "values": [d["composite_score"] for d in top]}],
        },
        "type": "bar",
        "colors": ["#5e64ff"],
    }
