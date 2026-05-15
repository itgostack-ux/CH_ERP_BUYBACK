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
    # Sales Invoice — exchange/trade-in reference
    # Locks the exchange credit to one customer+invoice; validated
    # server-side in hooks so staff cannot cross-apply credits.
    # ──────────────────────────────────────────────────────────────
    "Sales Invoice": [
        {
            "fieldname": "ch_exchange_section",
            "label": _("Exchange / Trade-In"),
            "fieldtype": "Section Break",
            "insert_after": "pos_profile",
            "collapsible": 1,
            "description": _(
                "Link a Buyback Exchange Order to apply the trade-in credit. "
                "Customer on the exchange order must match this invoice's customer."
            ),
        },
        {
            "fieldname": "ch_exchange_order",
            "label": _("Exchange Order"),
            "fieldtype": "Link",
            "options": "Buyback Exchange Order",
            "insert_after": "ch_exchange_section",
            "in_standard_filter": 1,
            "bold": 1,
            "description": _(
                "Set via the Apply Exchange API. "
                "Validates that the exchange order belongs to this customer."
            ),
        },
        {
            "fieldname": "ch_exchange_credit",
            "label": _("Exchange Credit Applied (₹)"),
            "fieldtype": "Currency",
            "insert_after": "ch_exchange_order",
            "read_only": 1,
            "description": _(
                "Buyback amount from the linked exchange order, applied as "
                "a trade-in credit on this invoice."
            ),
        },
    ],
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
    # ──────────────────────────────────────────────────────────────
    # Material Request — buyback pickup linkage
    # Auto-created Material Transfer requests that move bought-back devices
    # from store warehouses to the central Buyback Bin warehouse.
    # ──────────────────────────────────────────────────────────────
    "Material Request": [
        {
            "fieldname": "custom_buyback_order",
            "label": _("Buyback Order"),
            "fieldtype": "Link",
            "options": "Buyback Order",
            "insert_after": "set_warehouse",
            "read_only": 1,
            "in_standard_filter": 1,
            "description": _(
                "Set when this Material Request is auto-created to pick up a "
                "bought-back device from a store and route it to the Buyback Bin."
            ),
        },
    ],
}


def setup_custom_fields():
    """Create all custom fields. Safe to call multiple times (idempotent)."""
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    create_custom_fields(CUSTOM_FIELDS, update=False)
