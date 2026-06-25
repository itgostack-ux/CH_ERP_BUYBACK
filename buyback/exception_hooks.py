"""Close the loop on an approved 'Buyback Price Override' exception.

When such a CH Exception Request (referencing a Buyback Order) is approved, the
manager-approved buyback price is written back to **that order only** so its
payout reflects the decision (e.g. store requested ₹5,000 → manager approves →
order final price becomes ₹5,000). Coexists with the order's own
inspection-variance approval — this is the ad-hoc store-initiated path.
"""
import frappe
from frappe import _
from frappe.utils import flt

OVERRIDE_TYPE = "Buyback Price Override"
APPROVED_STATUSES = ("Approved", "Auto-Approved")
# Never rewrite the price once money has moved or the order is closed.
LOCKED_ORDER_STATUSES = ("Paid", "Closed", "Cancelled", "Rejected")


def apply_approved_buyback_price_override(doc, method=None):
    if (doc.get("exception_type") != OVERRIDE_TYPE
            or doc.get("reference_doctype") != "Buyback Order"
            or not doc.get("reference_name")
            or doc.get("status") not in APPROVED_STATUSES):
        return

    order = doc.reference_name
    if not frappe.db.exists("Buyback Order", order):
        return
    order_status = frappe.db.get_value("Buyback Order", order, "status")
    if order_status in LOCKED_ORDER_STATUSES:
        frappe.log_error(
            title="Buyback price override not applied",
            message=f"Exception {doc.name}: order {order} is {order_status}; price not changed.")
        return

    # Approved price = manager's resolution value if they set one, else the
    # store's requested price.
    price = flt(doc.get("resolution_value")) or flt(doc.get("requested_value"))
    if price <= 0:
        return

    cur = frappe.db.get_value("Buyback Order", order,
                              ["final_price", "approved_price"], as_dict=True) or {}
    if flt(cur.get("final_price")) == price and flt(cur.get("approved_price")) == price:
        return  # idempotent — already applied

    # final_price drives the payout; approved_price must equal it or the order's
    # validate guard resets it. Written together (and the approver) so they stay
    # consistent. db.set_value: the order is submittable, and final_price is not
    # recomputed in validate, so this persists. Scoped to this one order.
    updates = {
        "final_price": price,
        "approved_price": price,
        "approved_by": doc.get("approver") or frappe.session.user,
    }
    # If the order was waiting on price approval, this approval clears that gate.
    if order_status == "Awaiting Approval":
        updates["status"] = "Approved"
        if frappe.get_meta("Buyback Order").has_field("workflow_state"):
            updates["workflow_state"] = "Approved"

    frappe.db.set_value("Buyback Order", order, updates, update_modified=True)

    try:
        frappe.get_doc("Buyback Order", order).add_comment(
            "Comment",
            _("Buyback price set to ₹{0} via approved exception {1} (requested by {2}).").format(
                f"{price:,.2f}", doc.name, doc.get("requested_by") or ""))
    except Exception:
        pass
