"""Repair inconsistent Buyback Exchange Order state/docstatus combinations.

Why:
- Some historical rows ended up as status='Closed' with docstatus=2 (cancelled),
  which hides them from standard list views.
- Some rows have status/workflow_state drift due to server-side status updates
  that did not keep workflow_state aligned.

This patch is idempotent and safe to run multiple times.
"""

import frappe


def execute():
    if not frappe.db.table_exists("Buyback Exchange Order"):
        return

    has_workflow_state = frappe.db.has_column("Buyback Exchange Order", "workflow_state")

    # Restore mistakenly cancelled rows that are semantically closed.
    frappe.db.sql(
        """
        UPDATE `tabBuyback Exchange Order`
        SET docstatus = 1,
            status = 'Closed'
        WHERE docstatus = 2
          AND status = 'Closed'
        """
    )

    if has_workflow_state:
        frappe.db.sql(
            """
            UPDATE `tabBuyback Exchange Order`
            SET workflow_state = 'Closed'
            WHERE docstatus = 1
              AND status = 'Closed'
              AND IFNULL(workflow_state, '') != 'Closed'
            """
        )

        # Keep workflow_state aligned with status for active/submitted records.
        frappe.db.sql(
            """
            UPDATE `tabBuyback Exchange Order`
            SET workflow_state = status
            WHERE docstatus < 2
              AND IFNULL(status, '') != ''
              AND IFNULL(workflow_state, '') != status
            """
        )

    frappe.db.commit()
