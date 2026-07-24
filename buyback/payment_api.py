"""
Buyback Payment API — instant customer payouts via Bank of Baroda DigiNext.

This module provides whitelisted endpoints used by the POS / customer portal
to disburse the agreed buyback amount directly to the customer's bank account
or UPI handle using the bank's NEFT / RTGS / IMPS / IFT rails.

Architecture
------------
This module is a *thin orchestration layer* over the existing payments
plumbing in ``ch_payments``:

    Buyback Order  ──(this module)──►  ch_payments.api.create_bank_payment_request
                                                       │
                                                       ▼
                                              Bank Payment Request
                                                       │
                                          submit  ────►  maker-checker
                                                       │
                                          send_to_bank ────►  BoB DigiNext API
                                                       │
                                          ◄────── status callback / inquiry
                                                       │
                                          on Processed → Buyback Order.payments

Endpoints
---------
* ``buyback.payment_api.initiate_payout``         — create a draft BPR for an order
* ``buyback.payment_api.approve_and_send_payout`` — submit + push to bank (separate user)
* ``buyback.payment_api.get_payout_status``       — read current status
* ``buyback.payment_api.refresh_payout_status``   — call bank inquiry API
* ``buyback.payment_api.list_payouts``            — history of all BPRs for an order
* ``buyback.payment_api.retry_payout``            — retry a failed BPR

All endpoints follow the patterns in :mod:`buyback.api`:

* type-annotated parameters,
* permission checks via :func:`frappe.has_permission`,
* user-facing messages wrapped in :func:`frappe._`,
* audit-trail entries via :func:`buyback.utils.log_audit`.

Maker-checker compliance
------------------------
The bank-side workflow enforces maker-checker (BPR creator ≠ BPR submitter).
Therefore ``initiate_payout`` and ``approve_and_send_payout`` are kept as
two separate calls — the POS user creates, a finance/cashier user approves
and pushes to the bank.  No single endpoint performs both steps.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import flt

from buyback.exceptions import BuybackStatusError, BuybackValidationError
from buyback.utils import assert_buyback_scope, log_audit, require_configured_role


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

# Statuses where a payout may be initiated.  Anything outside this set is
# either not yet ready (no customer approval) or already in a terminal state.
_PAYOUT_INITIABLE_STATUSES = {
    "Approved",
    "OTP Verified",
}

# BPR statuses that count as "open" — used for idempotency checks.
_OPEN_BPR_STATUSES = (
    "Draft",
    "Approved",
    "Sent to Bank",
    "Accepted",
    "Pending Authorization",
    "In Progress",
    "Processed",
    "Reconciled",
)

# BPR statuses that allow a retry.
_RETRYABLE_BPR_STATUSES = ("Failed", "Rejected")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_order(buyback_order: str, permission_type: str = "read") -> "frappe.Document":
    """Fetch the Buyback Order and enforce read permission."""
    if not buyback_order:
        frappe.throw(_("Buyback Order is required"), exc=BuybackValidationError)

    order = frappe.get_doc("Buyback Order", buyback_order)
    order.check_permission(permission_type)
    assert_buyback_scope(store=order.store, company=order.company)
    return order


def _validate_payout_eligibility(order: "frappe.Document") -> None:
    """Raise unless the Buyback Order is in a state where a payout can start."""
    if order.docstatus == 2:
        frappe.throw(_("Buyback Order {0} is cancelled").format(order.name), exc=BuybackStatusError)

    status = order.workflow_state or order.status
    if status not in _PAYOUT_INITIABLE_STATUSES:
        frappe.throw(
            _("Buyback Order {0} is in status '{1}'. A payout can only be created when status is one of: {2}").format(
                order.name, status, ", ".join(sorted(_PAYOUT_INITIABLE_STATUSES))
            ),
            exc=BuybackStatusError,
        )

    if not flt(order.final_price) > 0:
        frappe.throw(
            _("Buyback Order {0} has no positive final_price; nothing to pay out").format(order.name),
            exc=BuybackValidationError,
        )

    if not getattr(order, "customer_approved", 0):
        frappe.throw(
            _("Buyback Order {0} has not been customer-approved yet").format(order.name),
            exc=BuybackStatusError,
        )

    payout_mode = (order.customer_payout_mode or "").strip()
    if not payout_mode:
        frappe.throw(
            _("Customer has not chosen a payout mode on Buyback Order {0}").format(order.name),
            exc=BuybackValidationError,
        )

    # Bank transfer requires account number + IFSC; UPI requires upi_id.
    if payout_mode in ("Bank Transfer", "NEFT", "RTGS", "IFT"):
        if not order.customer_bank_account_number or not order.customer_bank_ifsc:
            frappe.throw(
                _("Bank Account Number and IFSC are required for {0} payouts").format(payout_mode),
                exc=BuybackValidationError,
            )
    elif payout_mode == "UPI":
        if not getattr(order, "customer_upi_id", None):
            frappe.throw(
                _("UPI ID is required for UPI payouts on Buyback Order {0}").format(order.name),
                exc=BuybackValidationError,
            )


def _find_existing_bpr(buyback_order: str) -> str | None:
    """Return the name of an open (non-failed) Bank Payment Request for this order, if any."""
    return frappe.db.get_value(
        "Bank Payment Request",
        {
            "source_doctype": "Buyback Order",
            "source_document": buyback_order,
            "payment_status": ("in", _OPEN_BPR_STATUSES),
            "docstatus": ("!=", 2),
        },
        "name",
    )


def _serialize_bpr(bpr_name: str) -> dict:
    """Project a Bank Payment Request into a flat status dict for API responses."""
    if not bpr_name:
        return {}

    bpr = frappe.get_doc("Bank Payment Request", bpr_name)
    bpr.check_permission("read")
    if not bpr:
        return {}
    fields = (
        "name", "docstatus", "payment_status", "payment_mode", "transaction_amount",
        "customer_txn_ref", "bank_ref", "cms_ref", "utr_number", "dd_number",
        "value_date", "bank_error_code", "bank_error_description", "retry_count",
        "last_inquired_at", "payment_entry",
    )
    row = frappe._dict({field: bpr.get(field) for field in fields})
    row["bpr"] = row.pop("name")
    return row


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60, ip_based=False)
def initiate_payout(buyback_order: str, bank_profile: str | None = None) -> dict:
    """Create a draft Bank Payment Request for the buyback amount.

    The returned BPR is in Draft state — a separate user must then call
    :func:`approve_and_send_payout` to comply with maker-checker controls.

    Idempotent: if an open BPR already exists for the order it is returned
    unchanged instead of creating a duplicate.

    Args:
        buyback_order: name of the Buyback Order to pay out.
        bank_profile: optional Bank Integration Profile to override the
            site default configured in Bank Integration Settings.

    Returns:
        Dict with ``bpr`` (name), ``payment_status``, ``transaction_amount``,
        ``payment_mode``, ``customer_txn_ref`` and ``already_existed`` flag.
    """
    require_configured_role("payment_operation_roles", action=_("initiate Buyback payouts"))
    order = _load_order(buyback_order, "write")
    _validate_payout_eligibility(order)

    existing = _find_existing_bpr(order.name)
    if existing:
        out = _serialize_bpr(existing)
        out["already_existed"] = True
        return out

    # Defer to the central ch_payments factory — it knows how to map
    # Buyback Order → BPR fields (customer bank details, payout mode hint,
    # enrichment fields, beneficiary lookup, etc.).
    from ch_payments.api import create_bank_payment_request

    result = create_bank_payment_request(
        source_doctype="Buyback Order",
        source_name=order.name,
        bank_profile=bank_profile,
    )

    bpr_name = result["name"]
    log_audit(
        action="Payout Initiated",
        reference_doctype="Buyback Order",
        reference_name=order.name,
        new_value={
            "bpr": bpr_name,
            "amount": flt(order.final_price),
            "payout_mode": order.customer_payout_mode,
            "bank_profile": bank_profile or "site_default",
        },
        reason=f"Customer payout draft created via buyback.payment_api.initiate_payout",
    )

    out = _serialize_bpr(bpr_name)
    out["already_existed"] = False
    return out


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60, ip_based=False)
def approve_and_send_payout(bpr: str) -> dict:
    """Submit the Bank Payment Request and push it to the bank.

    Maker-checker: the caller must be different from the user who created
    the BPR — enforced by ``BankPaymentRequest._enforce_maker_checker``.

    Args:
        bpr: name of the Bank Payment Request to approve.

    Returns:
        Dict with the updated BPR snapshot (status / bank_ref / utr / cms_ref).
    """
    require_configured_role("payment_operation_roles", action=_("approve Buyback payouts"))
    if not bpr:
        frappe.throw(_("Bank Payment Request name is required"), exc=BuybackValidationError)

    bpr_doc = frappe.get_doc("Bank Payment Request", bpr)
    bpr_doc.check_permission("submit")

    source_doctype = bpr_doc.source_doctype
    source_name = bpr_doc.source_document
    if source_doctype != "Buyback Order":
        frappe.throw(
            _("Bank Payment Request {0} is not linked to a Buyback Order").format(bpr),
            exc=BuybackValidationError,
        )
    _load_order(source_name, "write")

    if bpr_doc.docstatus == 0:
        bpr_doc.submit()           # raises if maker == checker
    elif bpr_doc.docstatus == 2:
        frappe.throw(_("Bank Payment Request {0} is cancelled").format(bpr), exc=BuybackStatusError)

    # Submit moves it to "Approved".  send_to_bank pushes to the BoB API.
    if bpr_doc.payment_status == "Approved":
        bpr_doc.send_to_bank()

    log_audit(
        action="Payout Sent To Bank",
        reference_doctype="Buyback Order",
        reference_name=source_name,
        new_value=_serialize_bpr(bpr),
        reason="Approved + pushed to bank via buyback.payment_api.approve_and_send_payout",
    )

    return _serialize_bpr(bpr)


@frappe.whitelist()
def get_payout_status(buyback_order: str) -> dict:
    """Return the current payout status for a Buyback Order.

    Does not call the bank — purely a local read.  Use
    :func:`refresh_payout_status` to force a live bank inquiry.

    Returns:
        Dict with ``has_payout`` (bool) and, when a payout exists,
        the same fields as :func:`_serialize_bpr` plus the local
        ``payment_status`` on the order itself.
    """
    require_configured_role("app_access_roles", action=_("view Buyback payout status"))
    order = _load_order(buyback_order)

    bpr_name = _find_existing_bpr(order.name)
    if not bpr_name:
        # Fall back to most recent BPR for visibility, including failed ones.
        bpr_name = frappe.db.get_value(
            "Bank Payment Request",
            {"source_doctype": "Buyback Order", "source_document": order.name},
            "name",
            order_by="creation desc",
        )

    out = {
        "buyback_order": order.name,
        "order_payment_status": order.payment_status,
        "order_status": order.workflow_state or order.status,
        "has_payout": bool(bpr_name),
    }
    if bpr_name:
        out.update(_serialize_bpr(bpr_name))
    return out


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60, ip_based=False)
def refresh_payout_status(buyback_order: str) -> dict:
    """Call the bank's inquiry API for this order's latest payout and return the updated status."""
    require_configured_role("payment_operation_roles", action=_("refresh Buyback payouts"))
    order = _load_order(buyback_order, "write")

    bpr_name = _find_existing_bpr(order.name)
    if not bpr_name:
        frappe.throw(
            _("No open payout found for Buyback Order {0}").format(order.name),
            exc=BuybackStatusError,
        )

    bpr_doc = frappe.get_doc("Bank Payment Request", bpr_name)
    bpr_doc.check_permission("read")
    bpr_doc.inquire_status()       # mutates bpr_doc in DB
    return _serialize_bpr(bpr_name)


@frappe.whitelist()
def list_payouts(buyback_order: str) -> list[dict]:
    """Return the full history of payouts (BPRs) created for a Buyback Order."""
    require_configured_role("app_access_roles", action=_("view Buyback payouts"))
    order = _load_order(buyback_order)

    rows = frappe.get_list(
        "Bank Payment Request",
        filters={"source_doctype": "Buyback Order", "source_document": order.name},
        fields=[
            "name",
            "creation",
            "docstatus",
            "payment_status",
            "payment_mode",
            "transaction_amount",
            "customer_txn_ref",
            "bank_ref",
            "cms_ref",
            "utr_number",
            "bank_error_code",
            "retry_count",
        ],
        order_by="creation desc",
        limit_page_length=50,
    )
    for r in rows:
        r["bpr"] = r.pop("name")
    return rows


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=10, seconds=60, ip_based=False)
def retry_payout(buyback_order: str) -> dict:
    """Retry the most recent failed/rejected payout for this Buyback Order."""
    require_configured_role("payment_operation_roles", action=_("retry Buyback payouts"))
    order = _load_order(buyback_order, "write")

    bpr_name = frappe.db.get_value(
        "Bank Payment Request",
        {
            "source_doctype": "Buyback Order",
            "source_document": order.name,
            "payment_status": ("in", _RETRYABLE_BPR_STATUSES),
            "docstatus": 1,
        },
        "name",
        order_by="creation desc",
    )
    if not bpr_name:
        frappe.throw(
            _("No retryable payout found for Buyback Order {0}").format(order.name),
            exc=BuybackStatusError,
        )

    bpr_doc = frappe.get_doc("Bank Payment Request", bpr_name)
    bpr_doc.check_permission("submit")
    bpr_doc.retry_payment()

    log_audit(
        action="Payout Retried",
        reference_doctype="Buyback Order",
        reference_name=order.name,
        new_value=_serialize_bpr(bpr_name),
        reason=f"Manual retry via buyback.payment_api.retry_payout",
    )

    return _serialize_bpr(bpr_name)
