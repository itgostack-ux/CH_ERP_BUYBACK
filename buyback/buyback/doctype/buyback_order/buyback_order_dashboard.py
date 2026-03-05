from frappe import _


def get_data():
    return {
        "fieldname": "buyback_order",
        "non_standard_fieldnames": {},
        "transactions": [
            {
                "label": _("Exchange"),
                "items": ["Buyback Exchange Order"],
            },
        ],
    }
