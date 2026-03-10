# Copyright (c) 2026, GoStack and contributors
# R3 — Branch Performance
# Per-store metrics: quotes, inspections, approvals, settlements, conversion, TAT, SLA.

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
        {"fieldname": "assessments", "label": _("Assessments"), "fieldtype": "Int", "width": 100},
        {"fieldname": "app_assessments", "label": _("App Assessments"), "fieldtype": "Int", "width": 120},
        {"fieldname": "inspections", "label": _("Inspections"), "fieldtype": "Int", "width": 100},
        {"fieldname": "approved", "label": _("Approved"), "fieldtype": "Int", "width": 90},
        {"fieldname": "settled", "label": _("Settled"), "fieldtype": "Int", "width": 80},
        {"fieldname": "settled_value", "label": _("Settled ₹"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "conversion_pct", "label": _("Conversion %"), "fieldtype": "Percent", "width": 110},
        {"fieldname": "avg_tat_min", "label": _("Avg TAT (min)"), "fieldtype": "Float", "width": 110},
        {"fieldname": "sla_breach_pct", "label": _("SLA Breach %"), "fieldtype": "Percent", "width": 110},
        {"fieldname": "mismatch_pct", "label": _("Mismatch %"), "fieldtype": "Percent", "width": 110},
    ]


def get_data(filters):
    dc = date_condition("creation", filters)
    sc = standard_conditions(filters)

    # Assessments per store
    assessment_map = {}
    for r in frappe.db.sql(f"""
        SELECT store, COUNT(*) as cnt,
               SUM(CASE WHEN source='App Diagnosis' THEN 1 ELSE 0 END) as app_cnt
        FROM `tabBuyback Assessment` WHERE {dc} {sc} AND store IS NOT NULL
        GROUP BY store
    """, as_dict=1):
        assessment_map[r.store] = r

    # Orders per store
    odc = dc.replace("creation", "o.creation")
    osc = standard_conditions(filters, alias="o.")
    order_map = {}
    for r in frappe.db.sql(f"""
        SELECT o.store,
               COUNT(*) as total,
               SUM(CASE WHEN o.customer_approved=1 THEN 1 ELSE 0 END) as approved,
               SUM(CASE WHEN o.status IN ('Paid','Closed') THEN 1 ELSE 0 END) as settled,
               COALESCE(SUM(CASE WHEN o.status IN ('Paid','Closed') THEN o.total_paid ELSE 0 END),0) as settled_value,
               ROUND(AVG(TIMESTAMPDIFF(MINUTE, o.creation, COALESCE(o.modified, NOW()))),1) as avg_tat_min
        FROM `tabBuyback Order` o
        WHERE o.docstatus<2 AND {odc} {osc} AND o.store IS NOT NULL
        GROUP BY o.store
    """, as_dict=1):
        order_map[r.store] = r

    # Inspections per store
    idc = dc.replace("creation", "i.creation")
    insp_map = {}
    for r in frappe.db.sql(f"""
        SELECT i.store, COUNT(*) as cnt,
               ROUND(AVG(i.mismatch_percentage),1) as avg_mm
        FROM `tabBuyback Inspection` i
        WHERE i.status='Completed' AND {idc} AND i.store IS NOT NULL
        GROUP BY i.store
    """, as_dict=1):
        insp_map[r.store] = r

    # SLA breaches per store
    sla_map = {}
    for r in frappe.db.sql(f"""
        SELECT store, COUNT(*) as breaches
        FROM `tabBuyback SLA Log`
        WHERE breached=1 AND {dc} {sc} AND store IS NOT NULL
        GROUP BY store
    """, as_dict=1):
        sla_map[r.store] = r.breaches

    all_stores = set(list(assessment_map.keys()) + list(order_map.keys()))
    data = []
    for s in sorted(all_stores):
        am = assessment_map.get(s, {})
        om = order_map.get(s, {})
        im = insp_map.get(s, {})
        assessments = am.get("cnt", 0) or 0
        settled = om.get("settled", 0) or 0
        total_orders = om.get("total", 0) or 0
        breaches = sla_map.get(s, 0)

        data.append({
            "store": s,
            "assessments": assessments,
            "app_assessments": am.get("app_cnt", 0) or 0,
            "inspections": im.get("cnt", 0) or 0,
            "approved": om.get("approved", 0) or 0,
            "settled": settled,
            "settled_value": om.get("settled_value", 0),
            "conversion_pct": round(settled / assessments * 100, 1) if assessments else 0,
            "avg_tat_min": om.get("avg_tat_min", 0) or 0,
            "sla_breach_pct": round(breaches / total_orders * 100, 1) if total_orders else 0,
            "mismatch_pct": im.get("avg_mm", 0) or 0,
        })

    data.sort(key=lambda x: x.get("settled_value", 0), reverse=True)
    return data


def get_chart(data):
    if not data:
        return None
    top = data[:15]
    return {
        "data": {
            "labels": [d["store"] for d in top],
            "datasets": [{"name": _("Settled ₹"), "values": [d["settled_value"] for d in top]}],
        },
        "type": "bar",
        "colors": ["#5e64ff"],
    }
