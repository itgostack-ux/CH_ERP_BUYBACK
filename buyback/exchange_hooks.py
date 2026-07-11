"""
Exchange Order ↔ Sales Invoice validation hooks.

Registered in hooks.py as:
    "Sales Invoice": {"validate": "buyback.exchange_hooks.validate_exchange_order_customer_match"}

Prevents staff from applying an exchange credit that belongs to a different
customer — the most common mistake in multi-counter phone retail stores.

Market standard (Apple, Samsung dealer, Vijay Sales, Croma):
  Each trade-in / exchange quotation is locked to one customer.
  When billing, the POS looks up exchange orders by customer, pre-fills
  the trade-in amount, and stamps the exchange order number on the invoice.
  Attempting to apply another customer's exchange order throws a hard error.
"""

import frappe
from frappe import _
from frappe.utils import flt

from buyback.exceptions import BuybackValidationError


def move_traded_device_to_buyback_on_invoice(doc, method=None) -> None:
    """On completion of an exchange sale invoice, retire the traded-in device
    from the reserved sellable stock into the store's Buyback bin.

    During the exchange the old device sits in the SELLABLE warehouse tagged
    RESERVED — held for the buyback customer, not sellable to other walk-ins.
    Once the exchange invoice is submitted the exchange is done, so the old
    device follows the standard buyback path into quarantine/refurb (a store
    executive later promotes it Buyback → Sellable to resell). Idempotent +
    best-effort: the physical move must not roll back a submitted invoice.
    """
    exchange_order = doc.get("ch_exchange_order")
    if not exchange_order or not frappe.db.exists("Buyback Exchange Order", exchange_order):
        return
    # Stamp the completing invoice on the exchange order (audit + single-use).
    if not frappe.db.get_value("Buyback Exchange Order", exchange_order, "sales_invoice"):
        frappe.db.set_value(
            "Buyback Exchange Order", exchange_order, "sales_invoice", doc.name,
            update_modified=False,
        )
    try:
        exo = frappe.get_doc("Buyback Exchange Order", exchange_order)
        exo._move_old_device_to_buyback_bin()
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Exchange invoice {doc.name}: move traded device to Buyback bin failed ({exchange_order})",
        )


def restore_traded_device_on_invoice_cancel(doc, method=None) -> None:
    """Exchange invoice cancelled → reverse of the on_submit move: bring the
    traded-in device back out of the Buyback bin into the store's SELLABLE
    warehouse, RESERVED for the original buyback customer (so it can be
    re-invoiced or handed back to that same customer). Also clears the
    single-use invoice stamp so a corrected exchange invoice can be raised.
    """
    exchange_order = doc.get("ch_exchange_order")
    if not exchange_order or not frappe.db.exists("Buyback Exchange Order", exchange_order):
        return
    # Release the single-use invoice stamp so the exchange can be re-invoiced.
    if frappe.db.get_value("Buyback Exchange Order", exchange_order, "sales_invoice") == doc.name:
        frappe.db.set_value(
            "Buyback Exchange Order", exchange_order, "sales_invoice", None,
            update_modified=False,
        )
    try:
        exo = frappe.get_doc("Buyback Exchange Order", exchange_order)
        exo._restore_old_device_to_reserved()
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Exchange invoice {doc.name}: restore traded device to reserved failed ({exchange_order})",
        )


def validate_exchange_order_customer_match(doc, method=None) -> None:
    """Validate that ch_exchange_order belongs to the same customer as this SI.

    Called on every Sales Invoice validate (save + submit).
    Raises BuybackValidationError if customers don't match.
    """
    exchange_order = doc.get("ch_exchange_order")
    if not exchange_order:
        return  # No exchange linked — nothing to check

    exo_customer = frappe.db.get_value(
        "Buyback Exchange Order", exchange_order, "customer"
    )

    if not exo_customer:
        frappe.throw(
            _("Exchange Order {0} does not exist or has been deleted.").format(
                frappe.bold(exchange_order)
            ),
            exc=BuybackValidationError,
            title=_("Invalid Exchange Order"),
        )

    if exo_customer != doc.customer:
        frappe.throw(
            _(
                "Exchange Order {0} belongs to customer <b>{1}</b> but this "
                "invoice is for customer <b>{2}</b>. "
                "Remove the exchange order or change the customer."
            ).format(
                frappe.bold(exchange_order),
                exo_customer,
                doc.customer,
            ),
            exc=BuybackValidationError,
            title=_("Exchange Order Customer Mismatch"),
        )

    # Ensure the exchange credit field is populated
    if not flt(doc.get("ch_exchange_credit")):
        credit = frappe.db.get_value(
            "Buyback Exchange Order", exchange_order, "buyback_amount"
        )
        doc.ch_exchange_credit = flt(credit)

    # Hard-block: exchange order already applied to a different (non-cancelled) SI.
    # Market standard (Cashify, Samsung Exchange, Best Buy Trade-In, Apple Trade In,
    # Flipkart Reset): a trade-in credit is single-use. Reapplying it is a
    # duplicate-credit fraud vector — must throw, not warn.
    existing_si = frappe.db.get_value(
        "Buyback Exchange Order", exchange_order, "sales_invoice"
    )
    if existing_si and existing_si != doc.name:
        existing_docstatus = frappe.db.get_value(
            "Sales Invoice", existing_si, "docstatus"
        )
        # docstatus 2 = cancelled; only block if the prior SI is still Draft/Submitted
        if existing_docstatus in (0, 1):
            frappe.throw(
                _(
                    "Exchange Order {0} has already been applied to "
                    "Sales Invoice {1}. A trade-in credit can only be used once. "
                    "Cancel the prior invoice or use a different exchange order."
                ).format(frappe.bold(exchange_order), frappe.bold(existing_si)),
                exc=BuybackValidationError,
                title=_("Exchange Already Applied"),
            )

    # Cross-flow bridge: the same underlying Buyback Assessment must not have
    # been used on a POS invoice (ch_pos stamps `linked_pos_invoice` on the
    # Assessment when applied via POS). Chain:
    #   Buyback Exchange Order → buyback_order → buyback_assessment → linked_pos_invoice
    buyback_order = frappe.db.get_value(
        "Buyback Exchange Order", exchange_order, "buyback_order"
    )
    if buyback_order:
        buyback_assessment = frappe.db.get_value(
            "Buyback Order", buyback_order, "buyback_assessment"
        )
        if buyback_assessment:
            pos_si = frappe.db.get_value(
                "Buyback Assessment", buyback_assessment, "linked_pos_invoice"
            )
            if pos_si and pos_si != doc.name:
                pos_docstatus = frappe.db.get_value(
                    "Sales Invoice", pos_si, "docstatus"
                )
                if pos_docstatus in (0, 1):
                    frappe.throw(
                        _(
                            "The underlying Buyback Assessment {0} has already "
                            "been used as exchange credit on POS invoice {1}. "
                            "The same trade-in cannot be applied twice."
                        ).format(
                            frappe.bold(buyback_assessment),
                            frappe.bold(pos_si),
                        ),
                        exc=BuybackValidationError,
                        title=_("Exchange Already Applied (POS)"),
                    )
