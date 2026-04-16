from frappe import _


def get_dashboard_for_customer(data):
    """Add buyback transactions to the Customer dashboard.

    Also chains through ch_item_master's Customer dashboard override
    so that Warranty/VAS, Devices, Vouchers, and Exceptions links are
    not lost (only one override_doctype_dashboards winner per doctype;
    buyback loads after ch_item_master in apps.txt).
    """
    # ── Chain: apply ch_item_master's Customer dashboard additions first ──
    try:
        from ch_item_master.ch_item_master.overrides.customer_dashboard import get_data
        data = get_data(data)
    except Exception:
        pass  # ch_item_master not installed or changed — degrade gracefully

    # ── Buyback's own additions ──
    data["transactions"].append(
        {
            "label": _("Buyback"),
            "items": [
                "Buyback Assessment",
                "Buyback Order",
                "Buyback Inspection",
                "Buyback Exchange Order",
            ],
        }
    )
    return data


def get_dashboard_for_item(data):
    """Add buyback transactions to the Item dashboard.

    Buyback doctypes use ``item`` (not ``item_code``) as the link
    field, except Buyback Exchange Order which uses ``old_item``
    for the traded-in device and ``new_item`` for the replacement.
    """
    data["non_standard_fieldnames"].update(
        {
            "Buyback Assessment": "item",
            "Buyback Order": "item",
            "Buyback Inspection": "item",
            "Buyback Exchange Order": "old_item",
        }
    )
    data["transactions"].append(
        {
            "label": _("Buyback"),
            "items": [
                "Buyback Assessment",
                "Buyback Order",
                "Buyback Inspection",
                "Buyback Exchange Order",
            ],
        }
    )
    return data
