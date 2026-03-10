"""
Scheduled tasks for the Buyback app.
Registered via hooks.py scheduler_events.
"""

import frappe
from frappe.utils import nowdate, getdate


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
    )

    for name in expired:
        try:
            doc = frappe.get_doc("Buyback Assessment", name)
            doc.mark_expired()
            frappe.logger("buyback").info(f"Auto-expired assessment {name}")
        except Exception:
            frappe.log_error(f"Failed to expire assessment {name}", "Buyback Assessment Expiry")

    if expired:
        frappe.db.commit()

    return f"Expired {len(expired)} assessments"


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
    )

    for name in expired:
        try:
            doc = frappe.get_doc("CH OTP Log", name)
            doc.status = "Expired"
            doc.save(ignore_permissions=True)
        except Exception:
            frappe.log_error(
                title=f"Failed to expire OTP {name}",
            )

    if expired:
        frappe.db.commit()


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
