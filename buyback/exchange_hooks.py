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
