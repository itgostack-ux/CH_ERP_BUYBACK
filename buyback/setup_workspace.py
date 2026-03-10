"""
Setup the Buyback Workspace with shortcuts, links, and number cards.
Run via: bench --site erpnext.local execute buyback.setup_workspace.setup
"""

import frappe
from frappe.utils import nowdate


def setup():
    """Create or update the Buyback workspace."""
    _setup_number_cards()
    frappe.db.commit()  # commit cards first so workspace link validation passes
    _setup_workspace()
    frappe.db.commit()
    print("Buyback workspace configured successfully.")


def _setup_number_cards():
    """Create Number Cards for key buyback KPIs."""
    cards = [
        {
            "name": "Todays Buyback Assessments",
            "label": "Today's Assessments",
            "document_type": "Buyback Assessment",
            "function": "Count",
            "filters_json": '["Buyback Assessment","creation","Timespan","today"]]',
            "color": "#2490EF",
            "show_percentage_stats": 1,
            "stats_time_interval": "Daily",
        },
        {
            "name": "Pending Inspections",
            "label": "Pending Inspections",
            "document_type": "Buyback Inspection",
            "function": "Count",
            "filters_json": '[["Buyback Inspection","status","in",["Draft","In Progress"]]]',
            "color": "#ECAD4B",
            "show_percentage_stats": 0,
        },
        {
            "name": "Awaiting Approval Orders",
            "label": "Awaiting Approval",
            "document_type": "Buyback Order",
            "function": "Count",
            "filters_json": '[["Buyback Order","status","=","Awaiting Approval"]]',
            "color": "#CB2929",
            "show_percentage_stats": 0,
        },
        {
            "name": "Todays Buyback Revenue",
            "label": "Today's Revenue",
            "document_type": "Buyback Order",
            "function": "Sum",
            "aggregate_function_based_on": "final_price",
            "filters_json": '[["Buyback Order","status","in",["Paid","Closed"]],["Buyback Order","modified","Timespan","today"]]',
            "color": "#29CD42",
            "show_percentage_stats": 1,
            "stats_time_interval": "Daily",
        },
    ]

    for card_def in cards:
        if frappe.db.exists("Number Card", card_def["name"]):
            doc = frappe.get_doc("Number Card", card_def["name"])
            doc.update(card_def)
            doc.save(ignore_permissions=True)
        else:
            doc = frappe.get_doc({"doctype": "Number Card", **card_def})
            doc.insert(ignore_permissions=True)
        print(f"  Number Card: {card_def['name']}")


def _setup_workspace():
    """Create or update the BuyBack workspace."""
    ws_name = "BuyBack"

    if frappe.db.exists("Workspace", ws_name):
        ws = frappe.get_doc("Workspace", ws_name)
        ws.links = []
        ws.shortcuts = []
        ws.charts = []
        ws.number_cards = []
    else:
        ws = frappe.new_doc("Workspace")
        ws.name = ws_name
        ws.label = ws_name
        ws.module = "BuyBack"
        ws.icon = "shopping-cart"
        ws.is_hidden = 0

    # ── Number Card section (top) ──
    ws.append("number_cards", {"number_card_name": "Todays Buyback Assessments"})
    ws.append("number_cards", {"number_card_name": "Pending Inspections"})
    ws.append("number_cards", {"number_card_name": "Awaiting Approval Orders"})
    ws.append("number_cards", {"number_card_name": "Todays Buyback Revenue"})

    # ── Shortcuts ──
    for sc in [
        {"type": "DocType", "link_to": "Buyback Assessment", "label": "Assessments"},
        {"type": "DocType", "link_to": "Buyback Inspection", "label": "Inspections"},
        {"type": "DocType", "link_to": "Buyback Order", "label": "Orders"},
        {"type": "DocType", "link_to": "Buyback Exchange Order", "label": "Exchange"},
        {"type": "DocType", "link_to": "Buyback Settings", "label": "Settings"},
    ]:
        ws.append("shortcuts", sc)

    # ── Links (sidebar navigation) ──

    # --- Transaction Flow ---
    ws.append("links", {
        "type": "Card Break",
        "label": "Transaction Flow",
    })
    for dt in [
        "Buyback Assessment",
        "Buyback Inspection",
        "Buyback Order",
        "Buyback Exchange Order",
        "Buyback Audit Log",
    ]:
        ws.append("links", {
            "type": "Link",
            "link_type": "DocType",
            "link_to": dt,
            "label": dt,
        })

    # --- Masters ---
    ws.append("links", {
        "type": "Card Break",
        "label": "Masters",
    })
    for dt in [
        "Buyback Price Master",
        "Grade Master",
        "Buyback Question Bank",
        "Buyback Item Question Map",
        "Buyback Checklist Template",
        "Buyback Pricing Rule",
    ]:
        ws.append("links", {
            "type": "Link",
            "link_type": "DocType",
            "link_to": dt,
            "label": dt,
        })

    # --- Shared Masters (CH Core) ---
    ws.append("links", {
        "type": "Card Break",
        "label": "Shared Masters",
    })
    for dt_label in [
        ("Warehouse", "Stores"),
        ("Mode of Payment", "Payment Methods"),
        ("CH OTP Log", "OTP Log"),
    ]:
        dt, label = dt_label
        ws.append("links", {
            "type": "Link",
            "link_type": "DocType",
            "link_to": dt,
            "label": label,
        })

    # --- Settings ---
    ws.append("links", {
        "type": "Card Break",
        "label": "Settings & Configuration",
    })
    for dt in [
        "Buyback Settings",
        "Buyback SLA Settings",
    ]:
        ws.append("links", {
            "type": "Link",
            "link_type": "DocType",
            "link_to": dt,
            "label": dt,
        })

    # --- Reports ---
    report_categories = {
        "Daily Operations": [
            "Daily Ops Queue",
            "Customer Approval Pending",
            "Pending Confirmations",
            "Pending Payments",
            "Pending Settlement",
        ],
        "Funnel & Conversion": [
            "Buyback Funnel",
            "Source Mix",
            "Exchange Conversion",
            "Quote Accuracy",
        ],
        "Pricing & Quality": [
            "Price Variance",
            "Deduction Breakdown",
            "Mismatch Analysis",
            "Grade Distribution",
        ],
        "Performance": [
            "Branch Performance",
            "Store Scorecard",
            "Executive Performance",
            "Inspector Scorecard",
        ],
        "Finance & Settlement": [
            "Settlement Register",
            "Finance Payout Register",
            "Model Wise Buyback",
            "Category Trend",
        ],
        "Compliance & Audit": [
            "SLA Breach Report",
            "OTP Failure Report",
            "Manager Overrides Audit",
            "Duplicate IMEI Attempts",
        ],
    }

    for category, reports in report_categories.items():
        ws.append("links", {
            "type": "Card Break",
            "label": category,
        })
        for rpt in reports:
            ws.append("links", {
                "type": "Link",
                "link_type": "Report",
                "link_to": rpt,
                "label": rpt,
                "is_query_report": 1,
            })

    # Build visual content blocks
    ws.content = _build_content()

    ws.flags.ignore_links = True
    ws.save(ignore_permissions=True)
    print(f"  Workspace '{ws_name}' configured with {len(ws.links)} links")


def _build_content():
    """Build the workspace page layout JSON (number cards + shortcuts + links)."""
    import json

    blocks = [
        {
            "id": "buyback_header",
            "type": "header",
            "data": {
                "text": "<span class=\"h4\"><b>Buyback Dashboard</b></span>",
                "col": 12,
            },
        },
        {
            "id": "nc_assessments",
            "type": "number_card",
            "data": {"number_card_name": "Todays Buyback Assessments", "col": 3},
        },
        {
            "id": "nc_inspections",
            "type": "number_card",
            "data": {"number_card_name": "Pending Inspections", "col": 3},
        },
        {
            "id": "nc_approvals",
            "type": "number_card",
            "data": {"number_card_name": "Awaiting Approval Orders", "col": 3},
        },
        {
            "id": "nc_revenue",
            "type": "number_card",
            "data": {"number_card_name": "Todays Buyback Revenue", "col": 3},
        },
        {
            "id": "spacer_1",
            "type": "spacer",
            "data": {"col": 12},
        },
        {
            "id": "shortcuts_header",
            "type": "header",
            "data": {
                "text": "<span class=\"h4\"><b>Quick Actions</b></span>",
                "col": 12,
            },
        },
        {
            "id": "sc_assessment",
            "type": "shortcut",
            "data": {"shortcut_name": "Assessments", "col": 2},
        },
        {
            "id": "sc_quote",
            "type": "shortcut",
            "data": {"shortcut_name": "Quotes", "col": 2},
        },
        {
            "id": "sc_inspection",
            "type": "shortcut",
            "data": {"shortcut_name": "Inspections", "col": 2},
        },
        {
            "id": "sc_order",
            "type": "shortcut",
            "data": {"shortcut_name": "Orders", "col": 2},
        },
        {
            "id": "sc_exchange",
            "type": "shortcut",
            "data": {"shortcut_name": "Exchange", "col": 2},
        },
        {
            "id": "sc_settings",
            "type": "shortcut",
            "data": {"shortcut_name": "Settings", "col": 2},
        },
        {
            "id": "spacer_2",
            "type": "spacer",
            "data": {"col": 12},
        },
        {
            "id": "links_header",
            "type": "header",
            "data": {
                "text": "<span class=\"h4\"><b>Documents</b></span>",
                "col": 12,
            },
        },
        {
            "id": "card_txn",
            "type": "card",
            "data": {"card_name": "Transaction Flow", "col": 4},
        },
        {
            "id": "card_masters",
            "type": "card",
            "data": {"card_name": "Masters", "col": 4},
        },
        {
            "id": "card_settings",
            "type": "card",
            "data": {"card_name": "Settings & Configuration", "col": 4},
        },
        {
            "id": "spacer_3",
            "type": "spacer",
            "data": {"col": 12},
        },
        {
            "id": "card_shared",
            "type": "card",
            "data": {"card_name": "Shared Masters", "col": 4},
        },
        {
            "id": "spacer_reports",
            "type": "spacer",
            "data": {"col": 12},
        },
        {
            "id": "reports_header",
            "type": "header",
            "data": {
                "text": "<span class=\"h4\"><b>Reports</b></span>",
                "col": 12,
            },
        },
        {
            "id": "card_rpt_daily",
            "type": "card",
            "data": {"card_name": "Daily Operations", "col": 4},
        },
        {
            "id": "card_rpt_funnel",
            "type": "card",
            "data": {"card_name": "Funnel & Conversion", "col": 4},
        },
        {
            "id": "card_rpt_pricing",
            "type": "card",
            "data": {"card_name": "Pricing & Quality", "col": 4},
        },
        {
            "id": "card_rpt_perf",
            "type": "card",
            "data": {"card_name": "Performance", "col": 4},
        },
        {
            "id": "card_rpt_finance",
            "type": "card",
            "data": {"card_name": "Finance & Settlement", "col": 4},
        },
        {
            "id": "card_rpt_audit",
            "type": "card",
            "data": {"card_name": "Compliance & Audit", "col": 4},
        },
    ]
    return json.dumps(blocks)
