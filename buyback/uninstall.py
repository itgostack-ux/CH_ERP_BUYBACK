"""
Pre-uninstall cleanup for the Buyback app.
Pattern: India Compliance + HRMS both implement before_uninstall to
clean up custom roles, custom fields, and other artefacts.
"""

import frappe


BUYBACK_ROLES = [
    "Buyback Agent",
    "Buyback Manager",
    "Buyback Auditor",
    "Buyback Admin",
    "Buyback Store Manager",
]


def before_uninstall():
    """Clean up artefacts created by the Buyback app."""
    _delete_custom_roles()
    _delete_workflows()


def _delete_custom_roles():
    """Remove buyback-specific roles."""
    for role in BUYBACK_ROLES:
        if frappe.db.exists("Role", role):
            frappe.delete_doc("Role", role, ignore_permissions=True, force=True)
            frappe.logger("buyback").info(f"Deleted role: {role}")


def _delete_workflows():
    """Remove buyback workflows."""
    for wf_name in ("Buyback Order Workflow", "Buyback Exchange Workflow"):
        if frappe.db.exists("Workflow", wf_name):
            frappe.delete_doc("Workflow", wf_name, ignore_permissions=True, force=True)
            frappe.logger("buyback").info(f"Deleted workflow: {wf_name}")
