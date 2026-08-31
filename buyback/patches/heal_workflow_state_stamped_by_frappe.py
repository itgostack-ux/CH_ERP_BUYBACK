"""Heal `workflow_state` rows that Frappe stamped from doc_status.

Why:
    Frappe's ``Document.validate_workflow()`` calls
    ``set_workflow_state_on_action()`` for every action other than "save",
    including ``update_after_submit``. That helper writes the FIRST workflow
    state whose ``doc_status`` matches the document — "Paid" for Buyback Order
    and "New Device Delivered" for Buyback Exchange Order — whenever the
    document's current state does not carry the same doc_status.

    Both doctypes are submitted early in their lifecycle while their real
    states are still configured as doc_status 0, so ordinary field saves (KYC
    capture, payout preference, payment rows) silently rewrote the mirror. Once
    drifted, the next save raised
    "Workflow State transition not allowed from Paid to <status>" and the
    document could no longer be saved at all.

    The controllers now own ``validate_workflow()`` so the stamping can no
    longer happen; this patch repairs rows written before that fix.

Idempotent and safe to re-run.
"""

import frappe

_DOCTYPES = ("Buyback Order", "Buyback Exchange Order")


def execute():
    for doctype in _DOCTYPES:
        if not frappe.db.table_exists(doctype):
            continue
        if not frappe.db.has_column(doctype, "workflow_state"):
            continue

        drifted = frappe.db.sql(
            f"""
            SELECT COUNT(*)
            FROM `tab{doctype}`
            WHERE IFNULL(status, '') != ''
              AND IFNULL(workflow_state, '') != status
            """
        )[0][0]
        if not drifted:
            continue

        # `status` is the state machine; the mirror always follows it.
        frappe.db.sql(
            f"""
            UPDATE `tab{doctype}`
            SET workflow_state = status
            WHERE IFNULL(status, '') != ''
              AND IFNULL(workflow_state, '') != status
            """
        )
        frappe.logger().info(
            f"[heal_workflow_state_stamped_by_frappe] {doctype}: realigned {drifted} row(s)"
        )

    frappe.db.commit()
