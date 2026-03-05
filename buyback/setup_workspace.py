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
            "name": "Todays Buyback Quotes",
            "label": "Today's Quotes",
            "document_type": "Buyback Quote",
            "function": "Count",
            "filters_json": '[["Buyback Quote","creation","Timespan","today"]]',
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
    ws.append("number_cards", {"number_card_name": "Todays Buyback Quotes"})
    ws.append("number_cards", {"number_card_name": "Pending Inspections"})
    ws.append("number_cards", {"number_card_name": "Awaiting Approval Orders"})
    ws.append("number_cards", {"number_card_name": "Todays Buyback Revenue"})

    # ── Shortcuts ──
    for sc in [
        {"type": "DocType", "link_to": "Buyback Quote", "label": "New Quote"},
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
        "Buyback Quote",
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
    for dt in [
        "CH Store",
        "CH Payment Method",
        "CH State",
        "CH OTP Log",
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
    ws.append("links", {
        "type": "Link",
        "link_type": "DocType",
        "link_to": "Buyback Settings",
        "label": "Buyback Settings",
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
            "id": "nc_quotes",
            "type": "number_card",
            "data": {"number_card_name": "Todays Buyback Quotes", "col": 3},
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
            "id": "sc_quote",
            "type": "shortcut",
            "data": {"shortcut_name": "New Quote", "col": 3},
        },
        {
            "id": "sc_inspection",
            "type": "shortcut",
            "data": {"shortcut_name": "Inspections", "col": 3},
        },
        {
            "id": "sc_order",
            "type": "shortcut",
            "data": {"shortcut_name": "Orders", "col": 3},
        },
        {
            "id": "sc_exchange",
            "type": "shortcut",
            "data": {"shortcut_name": "Exchange", "col": 3},
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
                "text": "<span class=\"h4\"><b>Masters &amp; Reports</b></span>",
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
            "id": "card_shared",
            "type": "card",
            "data": {"card_name": "Shared Masters", "col": 4},
        },
    ]
    return json.dumps(blocks)
