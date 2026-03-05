from frappe import _


def get_dashboard_for_customer(data):
    """Add buyback transactions to the Customer dashboard."""
    data["transactions"].append(
        {
            "label": _("Buyback"),
            "items": [
                "Buyback Quote",
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
            "Buyback Quote": "item",
            "Buyback Order": "item",
            "Buyback Inspection": "item",
            "Buyback Exchange Order": "old_item",
        }
    )
    data["transactions"].append(
        {
            "label": _("Buyback"),
            "items": [
                "Buyback Quote",
                "Buyback Order",
                "Buyback Inspection",
                "Buyback Exchange Order",
            ],
        }
    )
    return data
