# Copyright (c) 2026, Congruence Holdings and contributors
# For license information, please see license.txt
"""Single source of truth for the buyback → exchange lifecycle.

Historically the POS flow (`ch_pos.api.pos_api._apply_exchange_credit`) and the
in-form flow (`Buyback Order._ensure_exchange_order_exists`) each created their
own version of an exchange record. That made the Phase A `exchange_hooks`
guardrails (customer match, no-dup, credit exhaustion) inert whenever POS was
the entry point.

This module exposes a **single** entrypoint that both surfaces call so an
exchange link always resolves to exactly one `Buyback Exchange Order` document
which the Phase A hooks then police uniformly.
"""

from __future__ import annotations

import frappe
from frappe import _


@frappe.whitelist()
def ensure_exchange_order_from_assessment(
    assessment_name: str,
    customer: str | None = None,
    mobile_no: str | None = None,
) -> dict:
    """Return (creating if needed) the canonical `Buyback Exchange Order`.

    Resolution order:
      1. If the Buyback Assessment already has ``linked_exchange_order`` — reuse.
      2. If a paid Buyback Order derived from this assessment exists and has
         ``settlement_type == "Exchange"``, delegate to
         :py:meth:`BuybackOrder._ensure_exchange_order_exists` so the exchange
         inherits the JE / SE trail.
      3. Otherwise create a lightweight ``Buyback Exchange Order`` seeded from
         the assessment (customer, item, IMEI, quoted price, store).

    Returns
    -------
    dict
        ``{"exchange_order": <name>, "buyback_order": <name or None>,
        "reused": bool, "source": "assessment" | "buyback_order"}``
    """
    if not assessment_name:
        frappe.throw(_("assessment_name is required"))

    if not frappe.db.exists("Buyback Assessment", assessment_name):
        frappe.throw(
            _("Buyback Assessment {0} not found").format(frappe.bold(assessment_name))
        )

    assessment = frappe.get_doc("Buyback Assessment", assessment_name)

    # ── 1. Reuse ────────────────────────────────────────────────────────
    existing = getattr(assessment, "linked_exchange_order", None)
    if existing and frappe.db.exists("Buyback Exchange Order", existing):
        return {
            "exchange_order": existing,
            "buyback_order": getattr(assessment, "buyback_order", None),
            "reused": True,
            "source": "assessment",
        }

    # ── 2. Delegate to Buyback Order when one exists ────────────────────
    buyback_order = getattr(assessment, "buyback_order", None)
    if not buyback_order:
        buyback_order = frappe.db.get_value(
            "Buyback Order",
            {"buyback_assessment": assessment_name, "docstatus": ["<", 2]},
            "name",
        )

    if buyback_order:
        bo = frappe.get_doc("Buyback Order", buyback_order)
        # Prefer BO's built-in idempotent creator when settlement is Exchange.
        creator = getattr(bo, "_ensure_exchange_order_exists", None)
        if callable(creator):
            try:
                creator()
                bo.reload()
            except Exception:
                # Never let the SoT helper hard-fail POS — bubble a clean
                # message instead.
                frappe.log_error(
                    frappe.get_traceback(),
                    f"exchange_lifecycle: BO._ensure_exchange_order_exists failed for {bo.name}",
                )
        exch = bo.get("exchange_order") or frappe.db.get_value(
            "Buyback Exchange Order",
            {"buyback_order": bo.name, "docstatus": ["<", 2]},
            "name",
        )
        if exch:
            _link_back_to_assessment(assessment, exch)
            return {
                "exchange_order": exch,
                "buyback_order": bo.name,
                "reused": True,
                "source": "buyback_order",
            }
        # Fall through — assessment has BO but no exchange yet.

    # ── 3. Create a lightweight exchange order from the assessment ──────
    price = frappe.utils.flt(
        getattr(assessment, "quoted_price", None)
        or getattr(assessment, "estimated_price", None)
    )
    exch_customer = customer or getattr(assessment, "customer", None)
    exch_mobile = mobile_no or getattr(assessment, "mobile_no", None)

    if not exch_customer:
        frappe.throw(
            _("Cannot create Exchange Order without a customer on Assessment {0}").format(
                frappe.bold(assessment_name)
            )
        )

    if not frappe.db.exists("DocType", "Buyback Exchange Order"):
        frappe.throw(
            _("DocType 'Buyback Exchange Order' is not installed on this site.")
        )

    doc = frappe.new_doc("Buyback Exchange Order")
    doc.update(
        {
            "buyback_assessment": assessment_name,
            "buyback_order": buyback_order,
            "customer": exch_customer,
            "mobile_no": exch_mobile,
            "store": getattr(assessment, "store", None),
            "old_item": getattr(assessment, "item", None),
            "old_imei_serial": getattr(assessment, "imei_serial", None)
            or getattr(assessment, "serial_no", None),
            "old_condition_grade": getattr(assessment, "condition_grade", None),
            "buyback_amount": price,
        }
    )
    doc.insert(ignore_permissions=True)
    _link_back_to_assessment(assessment, doc.name)
    return {
        "exchange_order": doc.name,
        "buyback_order": buyback_order,
        "reused": False,
        "source": "assessment",
    }


def _link_back_to_assessment(assessment, exchange_order_name: str) -> None:
    """Best-effort back-link on Buyback Assessment for future reuse.

    Silent if the target field isn't present — this lets the helper work on
    older sites that haven't picked up the Phase B custom field yet.
    """
    if not hasattr(assessment, "linked_exchange_order"):
        return
    if getattr(assessment, "linked_exchange_order", None) == exchange_order_name:
        return
    try:
        frappe.db.set_value(
            "Buyback Assessment",
            assessment.name,
            "linked_exchange_order",
            exchange_order_name,
            update_modified=False,
        )
    except Exception:
        # Not fatal for POS.
        frappe.log_error(
            frappe.get_traceback(),
            "exchange_lifecycle: linked_exchange_order backfill failed",
        )
