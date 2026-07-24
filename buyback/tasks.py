"""
Scheduled tasks for the Buyback app.
Registered via hooks.py scheduler_events.
"""

import frappe
from frappe.utils import nowdate

from buyback.utils import get_int_setting


def _scheduler_batch_limit() -> int:
    return min(get_int_setting("scheduler_batch_limit", 500), 5000)


def expire_assessments():
    """
    Daily job: auto-expire Buyback Assessments past their expires_on date.
    Moves status from Draft/Submitted → Expired.
    """
    expired = frappe.get_all(
        "Buyback Assessment",
        filters={
            "status": ["in", ["Draft", "Submitted"]],
            "expires_on": ["<", nowdate()],
        },
        pluck="name",
        order_by="expires_on asc, name asc",
        limit_page_length=_scheduler_batch_limit(),
    )

    processed = 0
    for index, name in enumerate(expired):
        savepoint = f"expire_assessment_{index}"
        frappe.db.savepoint(savepoint)
        try:
            doc = frappe.get_doc("Buyback Assessment", name)
            doc.mark_expired()
            processed += 1
            frappe.logger("buyback").info(f"Auto-expired assessment {name}")
        except Exception:
            frappe.db.rollback(save_point=savepoint)
            frappe.log_error(
                title=f"Buyback Assessment Expiry: {name}",
                message=frappe.get_traceback(),
            )

    return f"Expired {processed} assessments"


def expire_otps():
    """
    Hourly job: mark pending OTPs as expired if past expiry time.
    Uses ORM instead of raw SQL so that hooks (on_update) fire correctly.
    """
    from frappe.utils import now_datetime

    expired = frappe.get_all(
        "CH OTP Log",
        filters={
            "status": "Pending",
            "expires_at": ["<", now_datetime()],
        },
        pluck="name",
        order_by="expires_at asc, name asc",
        limit_page_length=_scheduler_batch_limit(),
    )

    if expired:
        frappe.db.set_value(
            "CH OTP Log",
            {"name": ("in", expired), "status": "Pending"},
            "status",
            "Expired",
            update_modified=False,
        )
    return len(expired)


def daily_buyback_summary():
    """
    Daily job: log a summary of buyback activity for the day.
    Could be extended to send email notifications.
    """
    today = nowdate()

    summary = {
        "assessments_created": frappe.db.count("Buyback Assessment", {"creation": [">=", today]}),
        "inspections_completed": frappe.db.count(
            "Buyback Inspection",
            {"status": "Completed", "inspection_completed_at": [">=", today]},
        ),
        "orders_paid": frappe.db.count(
            "Buyback Order",
            {"status": "Paid", "modified": [">=", today]},
        ),
        "exchanges_settled": frappe.db.count(
            "Buyback Exchange Order",
            {"status": "Settled", "modified": [">=", today]},
        ),
    }

    frappe.logger("buyback").info(f"Daily buyback summary: {summary}")
    return summary
