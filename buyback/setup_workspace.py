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
    _setup_sidebar()
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

    ws.type = ws.type or "Workspace"

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
        "Buyback SLA Log",
        "Refurbishment Order",
        "Store Credit Wallet",
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
        "Buyback IMEI Blacklist",
        "Buyback Question Bank",
        "Buyback Question Category",
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

    # --- Settings ---
    ws.append("links", {
        "type": "Card Break",
        "label": "Settings & Configuration",
    })
    for dt in [
        "Buyback Settings",
        "Buyback SLA Settings",
        "Buyback QA Test Run",
    ]:
        ws.append("links", {
            "type": "Link",
            "link_type": "DocType",
            "link_to": dt,
            "label": dt,
        })

    # --- Dashboard Pages ---
    ws.append("links", {
        "type": "Card Break",
        "label": "Dashboard Pages",
    })
    for page_name, label in [
        ("buyback-hub", "Buyback Hub"),
        ("store-manager-dashboard", "Store Manager Dashboard"),
        ("operations-dashboard", "Operations Dashboard"),
        ("finance-dashboard", "Finance Dashboard"),
        ("compliance-dashboard", "Compliance Dashboard"),
        ("category-manager-dashboard", "Category Manager Dashboard"),
    ]:
        if frappe.db.exists("Page", page_name):
            ws.append("links", {
                "type": "Link",
                "link_type": "Page",
                "link_to": page_name,
                "label": label,
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


def _setup_sidebar():
    """Create v16 Workspace Sidebar entries for BuyBack.

    Frappe v16 uses Workspace Sidebar for the left navigation. Keep dashboard
    hubs, daily transaction documents, configuration, reports, and audit links
    visible from the sidebar, matching enterprise launchpad patterns used by
    SAP Fiori / Oracle Retail / Dynamics role centers.
    """
    title = "BuyBack"
    if frappe.db.exists("Workspace Sidebar", title):
        sidebar = frappe.get_doc("Workspace Sidebar", title)
        sidebar.items = []
    else:
        sidebar = frappe.new_doc("Workspace Sidebar")
        sidebar.title = title
        sidebar.header_icon = "shopping-cart"
        sidebar.module = "BuyBack"
        sidebar.standard = 0

    rows = [
        {"label": "Home", "link_type": "Workspace", "link_to": "BuyBack"},
        {"type": "Section Break", "label": "Dashboards", "indent": 1, "keep_closed": 0},
        {"label": "Buyback Hub", "link_type": "Page", "link_to": "buyback-hub", "child": 1},
        {"label": "Store Manager Dashboard", "link_type": "Page", "link_to": "store-manager-dashboard", "child": 1},
        {"label": "Operations Dashboard", "link_type": "Page", "link_to": "operations-dashboard", "child": 1},
        {"label": "Finance Dashboard", "link_type": "Page", "link_to": "finance-dashboard", "child": 1},
        {"label": "Compliance Dashboard", "link_type": "Page", "link_to": "compliance-dashboard", "child": 1},
        {"label": "Category Manager Dashboard", "link_type": "Page", "link_to": "category-manager-dashboard", "child": 1},
        {"type": "Section Break", "label": "Transactions", "indent": 1, "keep_closed": 0},
        {"label": "Assessments", "link_type": "DocType", "link_to": "Buyback Assessment", "child": 1},
        {"label": "Inspections", "link_type": "DocType", "link_to": "Buyback Inspection", "child": 1},
        {"label": "Orders", "link_type": "DocType", "link_to": "Buyback Order", "child": 1},
        {"label": "Exchange Orders", "link_type": "DocType", "link_to": "Buyback Exchange Order", "child": 1},
        {"label": "Refurbishment Orders", "link_type": "DocType", "link_to": "Refurbishment Order", "child": 1},
        {"label": "Store Credit Wallet", "link_type": "DocType", "link_to": "Store Credit Wallet", "child": 1},
        {"type": "Section Break", "label": "Reports & Audit", "indent": 1, "keep_closed": 0},
        {"label": "Daily Ops Queue", "link_type": "Report", "link_to": "Daily Ops Queue", "child": 1},
        {"label": "Settlement Register", "link_type": "Report", "link_to": "Settlement Register", "child": 1},
        {"label": "SLA Breach Report", "link_type": "Report", "link_to": "SLA Breach Report", "child": 1},
        {"label": "Audit Log", "link_type": "DocType", "link_to": "Buyback Audit Log", "child": 1},
        {"label": "SLA Log", "link_type": "DocType", "link_to": "Buyback SLA Log", "child": 1},
        {"type": "Section Break", "label": "Setup", "indent": 1, "keep_closed": 1},
        {"label": "Price Master", "link_type": "DocType", "link_to": "Buyback Price Master", "child": 1},
        {"label": "Grade Master", "link_type": "DocType", "link_to": "Grade Master", "child": 1},
        {"label": "Question Category", "link_type": "DocType", "link_to": "Buyback Question Category", "child": 1},
        {"label": "Settings", "link_type": "DocType", "link_to": "Buyback Settings", "child": 1},
        {"label": "SLA Settings", "link_type": "DocType", "link_to": "Buyback SLA Settings", "child": 1},
        {"label": "QA Test Runs", "link_type": "DocType", "link_to": "Buyback QA Test Run", "child": 1},
    ]
    for row in rows:
        link_type = row.get("link_type", "DocType")
        link_to = row.get("link_to")
        if link_type == "DocType" and link_to and not frappe.db.exists("DocType", link_to):
            continue
        if link_type == "Report" and link_to and not frappe.db.exists("Report", link_to):
            continue
        if link_type == "Page" and link_to and not frappe.db.exists("Page", link_to):
            continue
        if link_type == "Workspace" and link_to and not frappe.db.exists("Workspace", link_to):
            continue
        sidebar.append("items", {
            "type": row.get("type", "Link"),
            "label": row.get("label"),
            "link_type": link_type,
            "link_to": link_to,
            "child": row.get("child", 0),
            "indent": row.get("indent", 0),
            "collapsible": row.get("collapsible", 1),
            "keep_closed": row.get("keep_closed", 0),
        })

    sidebar.flags.ignore_permissions = True
    sidebar.save(ignore_permissions=True) if sidebar.name else sidebar.insert(ignore_permissions=True)
    print(f"  Workspace Sidebar '{title}' configured with {len(sidebar.items)} items")


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
        {
            "id": "card_dashboards",
            "type": "card",
            "data": {"card_name": "Dashboard Pages", "col": 4},
        },
    ]
    return json.dumps(blocks)
