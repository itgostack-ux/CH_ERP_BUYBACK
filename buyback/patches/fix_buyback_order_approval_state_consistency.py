"""Repair Buyback Order approval flags and workflow state drift.

Why:
- Server-side status updates historically changed `status` without updating
  `workflow_state`.
- `requires_approval` was only ever set to 1, so rows could stay marked as
  manager-required after a price was reduced below the configured threshold.

This patch is idempotent and safe to run multiple times.
"""

import frappe


def execute():
    if not frappe.db.table_exists("Buyback Order"):
        return

    threshold = (
        frappe.db.get_single_value("Buyback Settings", "require_manager_approval_above")
        or 0
    )
    has_workflow_state = frappe.db.has_column("Buyback Order", "workflow_state")

    frappe.db.sql(
        """
        UPDATE `tabBuyback Order`
        SET requires_approval = CASE
            WHEN COALESCE(final_price, 0) > %(threshold)s THEN 1
            ELSE 0
        END
        WHERE docstatus < 2
        """,
        {"threshold": threshold},
    )

    # Submitted rows stuck in Draft should be pushed to their computed state.
    frappe.db.sql(
        """
        UPDATE `tabBuyback Order`
        SET status = CASE
            WHEN COALESCE(requires_approval, 0) = 1 THEN 'Awaiting Approval'
            ELSE 'Approved'
        END
        WHERE docstatus = 1
          AND status = 'Draft'
        """
    )

    # Pending approval is only needed while the recalculated flag is true.
    frappe.db.sql(
        """
        UPDATE `tabBuyback Order`
        SET status = 'Approved'
        WHERE docstatus < 2
          AND status = 'Awaiting Approval'
          AND COALESCE(requires_approval, 0) = 0
        """
    )

    # Auto-approved rows whose price now requires manager approval should move
    # back to the approval queue if no manager approved that specific amount.
    frappe.db.sql(
        """
        UPDATE `tabBuyback Order`
        SET status = 'Awaiting Approval',
            approved_by = NULL,
            approved_price = 0,
            approval_date = NULL
        WHERE docstatus < 2
          AND status = 'Approved'
          AND COALESCE(requires_approval, 0) = 1
          AND (
              IFNULL(approved_by, '') = ''
              OR COALESCE(approved_price, 0) != COALESCE(final_price, 0)
          )
        """
    )

    if has_workflow_state:
        frappe.db.sql(
            """
            UPDATE `tabBuyback Order`
            SET workflow_state = status
            WHERE docstatus < 2
              AND IFNULL(status, '') != ''
              AND IFNULL(workflow_state, '') != status
            """
        )

    frappe.db.commit()
