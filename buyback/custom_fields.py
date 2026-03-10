"""
Custom fields added to ERPNext core DocTypes by the Buyback app.
Applied via ``frappe.custom_field.create_custom_fields()`` on install/migrate.

Reuse strategy:
  - Serial No (native ERPNext) → IMEI/serial tracking for buyback lifecycle
    The core ``status`` field is managed by stock_ledger, so we use a
    separate ``ch_buyback_*`` set of custom fields.
"""

from frappe import _

CUSTOM_FIELDS = {
    # ──────────────────────────────────────────────────────────────
    # Serial No — buyback lifecycle fields
    # ──────────────────────────────────────────────────────────────
    "Serial No": [
        {
            "fieldname": "ch_buyback_section",
            "label": _("Buyback History"),
            "fieldtype": "Section Break",
            "insert_after": "maintenance_status",
            "collapsible": 1,
            "description": _("Buyback lifecycle information for this serial/IMEI"),
        },
        {
            "fieldname": "ch_buyback_status",
            "label": _("Buyback Status"),
            "fieldtype": "Select",
            "options": "\nAvailable\nQuoted\nUnder Inspection\nBought Back\nExchanged",
            "insert_after": "ch_buyback_section",
            "in_standard_filter": 1,
            "bold": 1,
            "description": _(
                "Tracks the buyback lifecycle. Managed automatically by the "
                "Buyback module — not affected by stock movements."
            ),
        },
        {
            "fieldname": "ch_buyback_order",
            "label": _("Last Buyback Order"),
            "fieldtype": "Link",
            "options": "Buyback Order",
            "insert_after": "ch_buyback_status",
            "read_only": 1,
            "description": _("The most recent buyback order for this serial"),
        },
        {
            "fieldname": "ch_buyback_col_break",
            "fieldtype": "Column Break",
            "insert_after": "ch_buyback_order",
        },
        {
            "fieldname": "ch_buyback_date",
            "label": _("Last Buyback Date"),
            "fieldtype": "Date",
            "insert_after": "ch_buyback_col_break",
            "read_only": 1,
        },
        {
            "fieldname": "ch_buyback_price",
            "label": _("Last Buyback Price"),
            "fieldtype": "Currency",
            "insert_after": "ch_buyback_date",
            "read_only": 1,
        },
        {
            "fieldname": "ch_buyback_grade",
            "label": _("Last Buyback Grade"),
            "fieldtype": "Link",
            "options": "Grade Master",
            "insert_after": "ch_buyback_price",
            "read_only": 1,
        },
        {
            "fieldname": "ch_buyback_count",
            "label": _("Times Bought Back"),
            "fieldtype": "Int",
            "insert_after": "ch_buyback_grade",
            "read_only": 1,
            "default": "0",
            "description": _("Total number of buyback transactions for this IMEI"),
        },
        {
            "fieldname": "ch_buyback_customer",
            "label": _("Last Buyback Customer"),
            "fieldtype": "Link",
            "options": "Customer",
            "insert_after": "ch_buyback_count",
            "read_only": 1,
        },
    ],
    # ──────────────────────────────────────────────────────────────
    # Warehouse — store capability flags (replaces CH Store DocType)
    # ──────────────────────────────────────────────────────────────
    "Warehouse": [
        {
            "fieldname": "ch_store_section",
            "label": _("Store Settings"),
            "fieldtype": "Section Break",
            "insert_after": "disabled",
            "collapsible": 1,
            "description": _("Store capability flags used by GoGizmo / GoFix modules"),
        },
        {
            "fieldname": "ch_store_id",
            "label": _("Store ID"),
            "fieldtype": "Int",
            "insert_after": "ch_store_section",
            "description": _("Legacy numeric store identifier"),
        },
        {
            "fieldname": "ch_store_code",
            "label": _("Store Code"),
            "fieldtype": "Data",
            "insert_after": "ch_store_id",
            "in_standard_filter": 1,
            "description": _("Short alphanumeric code for the store (e.g. GOG-CHN-TNAGAR)"),
        },
        {
            "fieldname": "ch_store_col_break",
            "fieldtype": "Column Break",
            "insert_after": "ch_store_code",
        },
        {
            "fieldname": "ch_is_buyback_enabled",
            "label": _("Buyback Enabled"),
            "fieldtype": "Check",
            "insert_after": "ch_store_col_break",
            "default": "0",
            "description": _("Allow buyback transactions at this store/warehouse"),
        },
        {
            "fieldname": "ch_is_service_enabled",
            "label": _("Service Enabled"),
            "fieldtype": "Check",
            "insert_after": "ch_is_buyback_enabled",
            "default": "0",
            "description": _("Allow service/repair orders at this store/warehouse"),
        },
        {
            "fieldname": "ch_is_retail_enabled",
            "label": _("Retail Enabled"),
            "fieldtype": "Check",
            "insert_after": "ch_is_service_enabled",
            "default": "0",
            "description": _("Allow retail sales at this store/warehouse"),
        },
    ],
}


def setup_custom_fields():
    """Create all custom fields. Safe to call multiple times (idempotent)."""
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    create_custom_fields(CUSTOM_FIELDS, update=True)
