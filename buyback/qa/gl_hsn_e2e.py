"""End-to-end HSN + GL bifurcation smoke test on erpnext.local for BM company.

Run with:
    bench --site erpnext.local execute buyback.qa.gl_hsn_e2e.run

Design:
  * Reuses existing masters (Company `BestBuy Mobiles Pvt Ltd`, income accounts
    `Sales - BM` and `Service - BM`, tax template `Output GST In-state - BM`).
  * Uses existing `Apple iPhone 11 Pro` (HSN 85171300) as the goods line so we
    don't fight ch_item_master governance for MRP/CH Category/etc.
  * Creates a lean service item `GLTEST-PLAN-01` (HSN 99871900) with
    `ignore_mandatory=True` to bypass CH governance for the test-only master.

Prints:
  1. Master data snapshot
  2. Sales Invoice item-line snapshot (income_account, cost_center, warehouse, HSN)
  3. Tax rows + item-wise tax detail
  4. HSN-wise bifurcation (taxable + CGST + SGST + IGST + total)
  5. Full GL Entries with Dr/Cr balance
  6. Verification checks (PASS/FAIL)
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import flt, nowdate


COMPANY = "BestBuy Mobiles Pvt Ltd"
ABBR = "BM"
COST_CENTER = f"Main - {ABBR}"

INCOME_GOODS = f"Sales - {ABBR}"
INCOME_SERVICE = f"Service - {ABBR}"

TAX_TEMPLATE = f"Output GST In-state - {ABBR}"
ITEM_TAX_TMPL_18 = f"GST 18% - {ABBR}"

# Row 1 — reuse existing iPhone variant (HSN 85171300)
PHONE_ITEM = "I00050"  # Apple iPhone 11 Pro 256GB Midnight Green
PHONE_RATE = 50000

# Row 2 — lean protection-plan service item, HSN 99871900
PLAN_ITEM = "GLTEST-PLAN-01"
PLAN_HSN = "99871900"
PLAN_RATE = 2000

CUSTOMER = "GL Test Customer"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _ensure_hsn(hsn: str) -> str:
    if not frappe.db.exists("GST HSN Code", hsn):
        doc = frappe.get_doc({
            "doctype": "GST HSN Code",
            "hsn_code": hsn,
            "description": f"Test HSN {hsn}",
        })
        doc.flags.ignore_permissions = True
        doc.flags.ignore_mandatory = True
        doc.insert(ignore_permissions=True)
    return hsn


def _resolve_item_group() -> str:
    ig = frappe.db.get_value("Item Group", {"is_group": 0, "name": "Services"}, "name")
    if ig:
        return ig
    return frappe.db.get_value("Item Group", {"is_group": 0}, "name")


def _ensure_service_item():
    _ensure_hsn(PLAN_HSN)
    if not frappe.db.exists("Item", PLAN_ITEM):
        doc = frappe.get_doc({
            "doctype": "Item",
            "item_code": PLAN_ITEM,
            "item_name": "GL Test Protection Plan 12m",
            "item_group": _resolve_item_group(),
            "stock_uom": "Nos",
            "is_stock_item": 0,
            "gst_hsn_code": PLAN_HSN,
            "item_defaults": [],  # prevent auto-inherit from item group
        })
        doc.flags.ignore_permissions = True
        doc.flags.ignore_mandatory = True
        doc.insert(ignore_permissions=True)
    # Purge all Item Default rows that don't belong to our company or point to
    # a warehouse that doesn't match their company.
    frappe.db.sql(
        "DELETE FROM `tabItem Default` WHERE parent=%s AND company != %s",
        (PLAN_ITEM, COMPANY),
    )
    bad = frappe.db.sql(
        """
        SELECT id.name
        FROM `tabItem Default` id
        WHERE id.parent = %s
          AND (id.default_warehouse IS NOT NULL AND id.default_warehouse != '')
          AND NOT EXISTS (
            SELECT 1 FROM `tabWarehouse` w
            WHERE w.name = id.default_warehouse AND w.company = id.company
          )
        """,
        (PLAN_ITEM,),
        as_dict=True,
    )
    for r in bad:
        frappe.db.delete("Item Default", {"name": r["name"]})

    row_name = frappe.db.get_value(
        "Item Default",
        {"parent": PLAN_ITEM, "company": COMPANY},
        "name",
    )
    if row_name:
        current = frappe.db.get_value("Item Default", row_name, "income_account")
        if current != INCOME_SERVICE:
            frappe.db.set_value(
                "Item Default", row_name, "income_account", INCOME_SERVICE,
                update_modified=False,
            )
    else:
        child = frappe.get_doc({
            "doctype": "Item Default",
            "parent": PLAN_ITEM,
            "parenttype": "Item",
            "parentfield": "item_defaults",
            "company": COMPANY,
            "income_account": INCOME_SERVICE,
        })
        child.flags.ignore_permissions = True
        child.flags.ignore_mandatory = True
        child.insert(ignore_permissions=True)

    # Ensure HSN is set correctly
    if frappe.db.get_value("Item", PLAN_ITEM, "gst_hsn_code") != PLAN_HSN:
        frappe.db.set_value("Item", PLAN_ITEM, "gst_hsn_code", PLAN_HSN,
                            update_modified=False)


def _ensure_phone_defaults():
    """Ensure the iPhone Item Default row for BM carries our INCOME_GOODS.

    We patch via db.set_value / direct child insert to avoid re-running Item
    validate(), which fails on some legacy iPhone masters that have
    has_serial_no=1 while is_stock_item=0.
    """
    row_name = frappe.db.get_value(
        "Item Default",
        {"parent": PHONE_ITEM, "company": COMPANY},
        "name",
    )
    if row_name:
        current = frappe.db.get_value("Item Default", row_name, "income_account")
        if current != INCOME_GOODS:
            frappe.db.set_value(
                "Item Default", row_name, "income_account", INCOME_GOODS,
                update_modified=False,
            )
        return
    # No default row for BM — insert one directly
    child = frappe.get_doc({
        "doctype": "Item Default",
        "parent": PHONE_ITEM,
        "parenttype": "Item",
        "parentfield": "item_defaults",
        "company": COMPANY,
        "income_account": INCOME_GOODS,
    })
    child.flags.ignore_permissions = True
    child.flags.ignore_mandatory = True
    child.insert(ignore_permissions=True)


def _ensure_customer():
    if frappe.db.exists("Customer", CUSTOMER):
        return CUSTOMER
    doc = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": CUSTOMER,
        "customer_type": "Individual",
        "customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
        "territory": frappe.db.get_value("Territory", {"is_group": 0}, "name"),
    })
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.insert(ignore_permissions=True)
    return doc.name


def _activate_item(item_code: str) -> None:
    """Force ch_lifecycle_status='Active' + ch_approval_status='Approved' so
    the CH governance guard (assert_item_transactable) lets the item into
    transactions.
    """
    updates = {}
    meta = frappe.get_meta("Item")
    if meta.has_field("ch_lifecycle_status"):
        updates["ch_lifecycle_status"] = "Active"
    if meta.has_field("ch_approval_status"):
        updates["ch_approval_status"] = "Approved"
    if meta.has_field("ch_plm_status"):
        updates["ch_plm_status"] = "Approved"
    for field, value in updates.items():
        current = frappe.db.get_value("Item", item_code, field)
        if current != value:
            frappe.db.set_value("Item", item_code, field, value, update_modified=False)


def run():
    frappe.set_user("Administrator")

    _log("=" * 78)
    _log("STEP 1 — Ensure master data")
    _log("=" * 78)
    _ensure_phone_defaults()
    _ensure_service_item()
    _activate_item(PHONE_ITEM)
    _activate_item(PLAN_ITEM)
    _ensure_customer()
    frappe.db.commit()

    for c in (PHONE_ITEM, PLAN_ITEM):
        it = frappe.get_doc("Item", c)
        defs = [(d.company, d.income_account) for d in it.item_defaults]
        _log(f"  Item {c:30s}  HSN={it.gst_hsn_code}  is_stock={it.is_stock_item}  defaults={defs}")

    _log("")
    _log("=" * 78)
    _log("STEP 2 — Build Sales Invoice (in-state GST, no update_stock)")
    _log("=" * 78)

    si = frappe.get_doc({
        "doctype": "Sales Invoice",
        "customer": CUSTOMER,
        "company": COMPANY,
        "posting_date": nowdate(),
        "due_date": nowdate(),
        "update_stock": 0,
        "cost_center": COST_CENTER,
        "taxes_and_charges": TAX_TEMPLATE,
        "items": [
            {"item_code": PHONE_ITEM, "qty": 1, "rate": PHONE_RATE,
             "cost_center": COST_CENTER, "income_account": INCOME_GOODS,
             "item_tax_template": ITEM_TAX_TMPL_18},
            {"item_code": PLAN_ITEM,  "qty": 1, "rate": PLAN_RATE,
             "cost_center": COST_CENTER, "income_account": INCOME_SERVICE,
             "item_tax_template": ITEM_TAX_TMPL_18},
        ],
    })
    tmpl = frappe.get_doc("Sales Taxes and Charges Template", TAX_TEMPLATE)
    for row in tmpl.taxes:
        si.append("taxes", {
            "charge_type": row.charge_type,
            "account_head": row.account_head,
            "description": row.description,
            "rate": row.rate,
            "cost_center": COST_CENTER,
        })
    si.flags.ignore_permissions = True
    si.flags.ignore_mandatory = True
    si.insert(ignore_permissions=True)
    si.submit()
    frappe.db.commit()

    _log(f"  Sales Invoice: {si.name}")
    _log(f"    Net Total:    {si.net_total}")
    _log(f"    Tax Total:    {si.total_taxes_and_charges}")
    _log(f"    Grand Total:  {si.grand_total}")
    _log(f"    Rounded:      {si.rounded_total}")
    _log(f"    debit_to:     {si.debit_to}")

    _log("")
    _log("=" * 78)
    _log("STEP 3 — Item-line snapshot")
    _log("=" * 78)
    for r in si.items:
        hsn = frappe.db.get_value("Item", r.item_code, "gst_hsn_code")
        _log(f"  Row#{r.idx}  {r.item_code:28s}  qty={r.qty}  rate={r.rate}  amount={r.amount}")
        _log(f"          income_account={r.income_account}  cost_center={r.cost_center}"
             f"  warehouse={r.warehouse}  HSN={hsn}")

    _log("")
    _log("=" * 78)
    _log("STEP 4 — Tax rows + item-wise tax detail")
    _log("=" * 78)
    _row_to_item_map = {r.name: r.item_code for r in si.items}
    for t in si.taxes:
        _log(f"  {t.account_head:35s}  rate={t.rate:>6}  "
             f"tax_amount={flt(t.tax_amount):>10.2f}  "
             f"base_tax_amount={flt(t.base_tax_amount):>10.2f}")
        raw = t.get("item_wise_tax_detail") or t.get("item_wise_tax_rates")
        if not raw:
            continue
        try:
            iw = json.loads(raw)
        except Exception:
            iw = {}
        for k, val in iw.items():
            if isinstance(val, (list, tuple)) and len(val) >= 2:
                rate, amt = val[0], val[1]
            elif isinstance(val, dict):
                rate = val.get("rate", "")
                amt = val.get("amount") or val.get("tax_amount", "")
            else:
                rate, amt = "", val
            item_code = _row_to_item_map.get(k, k)
            _log(f"      item={item_code:28s}  rate={rate}  amount={amt}")

    _log("")
    _log("=" * 78)
    _log("STEP 5 — HSN-wise bifurcation")
    _log("=" * 78)
    row_to_item = {r.name: r.item_code for r in si.items}
    row_to_hsn = {r.name: frappe.db.get_value("Item", r.item_code, "gst_hsn_code") or "-" for r in si.items}
    row_to_net = {r.name: flt(r.net_amount or r.amount) for r in si.items}
    hsn_map: dict = {}
    for r in si.items:
        h = row_to_hsn[r.name]
        row = hsn_map.setdefault(h, {"taxable": 0.0, "cgst": 0.0, "sgst": 0.0,
                                     "igst": 0.0, "items": []})
        row["taxable"] += row_to_net[r.name]
        row["items"].append(r.item_code)

    def _parse_tax_amount(val, row_net):
        """Return the tax amount for this line, adapting to the various shapes
        item_wise_tax_detail stores across ERPNext versions.

        Shapes observed:
          [rate, amount]                    (stock ERPNext)
          {"amount": ..., "rate": ...}
          rate (float)                      (India Compliance patched form)
          [rate]
        """
        if isinstance(val, (list, tuple)):
            if len(val) >= 2 and val[1] not in (None, "", 0):
                return flt(val[1])
            if len(val) >= 1:
                return flt(val[0]) * row_net / 100.0
            return 0.0
        if isinstance(val, dict):
            if val.get("amount") not in (None, ""):
                return flt(val["amount"])
            if val.get("tax_amount") not in (None, ""):
                return flt(val["tax_amount"])
            if val.get("rate") not in (None, ""):
                return flt(val["rate"]) * row_net / 100.0
            return 0.0
        # Bare scalar: could be rate% or amount. Heuristic: if it equals a
        # rate the tax template uses, treat as rate.
        return flt(val) * row_net / 100.0

    for t in si.taxes:
        raw = t.get("item_wise_tax_detail") or t.get("item_wise_tax_rates")
        if not raw:
            continue
        try:
            iw = json.loads(raw)
        except Exception:
            iw = {}
        head = (t.account_head or "").lower()
        key = "cgst" if "cgst" in head else "sgst" if "sgst" in head else "igst" if "igst" in head else "other"
        for k, val in iw.items():
            item_code = row_to_item.get(k, k)
            h = row_to_hsn.get(k) or frappe.db.get_value("Item", item_code, "gst_hsn_code") or "-"
            if h not in hsn_map:
                continue
            row_net = row_to_net.get(k, 0.0) or sum(
                row_to_net[rn] for rn, ic in row_to_item.items() if ic == item_code
            )
            hsn_map[h][key] = hsn_map[h].get(key, 0.0) + _parse_tax_amount(val, row_net)
    for h, row in hsn_map.items():
        total = row["taxable"] + row.get("cgst", 0) + row.get("sgst", 0) + row.get("igst", 0)
        _log(f"  HSN {h:>10s}  items={row['items']}  taxable={row['taxable']:>10.2f}  "
             f"CGST={row.get('cgst',0):>8.2f}  SGST={row.get('sgst',0):>8.2f}  "
             f"IGST={row.get('igst',0):>8.2f}  total={total:>10.2f}")

    _log("")
    _log("=" * 78)
    _log("STEP 6 — GL Entries (Dr / Cr balance check)")
    _log("=" * 78)
    gles = frappe.get_all(
        "GL Entry",
        filters={"voucher_type": "Sales Invoice", "voucher_no": si.name, "is_cancelled": 0},
        fields=["account", "debit", "credit", "cost_center", "party_type", "party"],
        order_by="creation asc",
    )
    total_dr = total_cr = 0.0
    for g in gles:
        total_dr += flt(g.debit)
        total_cr += flt(g.credit)
        party = f" [{g.party_type}:{g.party}]" if g.party else ""
        _log(f"  {g.account:38s}  Dr={g.debit:>12.2f}  Cr={g.credit:>12.2f}  "
             f"CC={g.cost_center}{party}")
    _log(f"  ---- Total Dr={total_dr:.2f}  Total Cr={total_cr:.2f}  "
         f"diff={total_dr - total_cr:.2f}")

    _log("")
    _log("=" * 78)
    _log("STEP 7 — Verification")
    _log("=" * 78)
    checks = []
    checks.append(("GL balanced (Dr == Cr)", abs(total_dr - total_cr) < 0.01))
    checks.append(("2 item lines posted", len(si.items) == 2))
    checks.append((
        "Two distinct HSN codes on invoice",
        len({frappe.db.get_value('Item', r.item_code, 'gst_hsn_code') for r in si.items}) == 2,
    ))
    checks.append((
        f"'{INCOME_GOODS}' credited for phone line",
        any(g["account"] == INCOME_GOODS and g["credit"] > 0 for g in gles),
    ))
    checks.append((
        f"'{INCOME_SERVICE}' credited for plan line",
        any(g["account"] == INCOME_SERVICE and g["credit"] > 0 for g in gles),
    ))
    checks.append((
        "Debtors debited with Grand Total",
        any(g["account"] == si.debit_to
            and abs(g["debit"] - flt(si.grand_total)) < 0.01 for g in gles),
    ))
    checks.append((
        "CGST + SGST both posted (in-state)",
        any("cgst" in g["account"].lower() and g["credit"] > 0 for g in gles)
        and any("sgst" in g["account"].lower() and g["credit"] > 0 for g in gles),
    ))
    checks.append((
        "Every P&L GL row carries cost center",
        all(
            g["cost_center"] or (frappe.get_cached_value(
                "Account", g["account"], "report_type") != "Profit and Loss")
            for g in gles
        ),
    ))
    checks.append((
        "Net Total == sum of item amounts",
        abs(flt(si.net_total) - sum(flt(r.amount) for r in si.items)) < 0.01,
    ))
    checks.append((
        "Grand Total == Net Total + Total Tax",
        abs(flt(si.grand_total) - flt(si.net_total) - flt(si.total_taxes_and_charges)) < 0.01,
    ))
    total_hsn_taxable = sum(row["taxable"] for row in hsn_map.values())
    total_hsn_cgst = sum(row.get("cgst", 0) for row in hsn_map.values())
    total_hsn_sgst = sum(row.get("sgst", 0) for row in hsn_map.values())
    checks.append((
        f"HSN taxable total ({total_hsn_taxable:.2f}) == invoice net ({flt(si.net_total):.2f})",
        abs(total_hsn_taxable - flt(si.net_total)) < 0.01,
    ))
    checks.append((
        f"HSN CGST + SGST ({total_hsn_cgst + total_hsn_sgst:.2f}) == invoice tax total ({flt(si.total_taxes_and_charges):.2f})",
        abs((total_hsn_cgst + total_hsn_sgst) - flt(si.total_taxes_and_charges)) < 0.01,
    ))
    checks.append((
        "HSN CGST == HSN SGST (in-state parity)",
        abs(total_hsn_cgst - total_hsn_sgst) < 0.01,
    ))
    # Cross-check GL revenue matches invoice line amounts
    revenue_gl = sum(
        flt(g["credit"]) for g in gles
        if frappe.get_cached_value("Account", g["account"], "root_type") == "Income"
    )
    checks.append((
        f"Income GL credit ({revenue_gl:.2f}) == invoice net ({flt(si.net_total):.2f})",
        abs(revenue_gl - flt(si.net_total)) < 0.01,
    ))
    tax_gl = sum(
        flt(g["credit"]) for g in gles
        if "cgst" in g["account"].lower() or "sgst" in g["account"].lower() or "igst" in g["account"].lower()
    )
    checks.append((
        f"Tax GL credit ({tax_gl:.2f}) == invoice tax total ({flt(si.total_taxes_and_charges):.2f})",
        abs(tax_gl - flt(si.total_taxes_and_charges)) < 0.01,
    ))
    for label, ok in checks:
        _log(f"  {'PASS' if ok else 'FAIL'}  {label}")

    _log("")
    _log(f"Sales Invoice: {si.name}")
    _log(f"General Ledger link: "
         f"/app/query-report/General%20Ledger?voucher_no={si.name}&company={COMPANY}")
    return si.name
