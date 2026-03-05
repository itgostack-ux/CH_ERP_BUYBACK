from frappe import _


def get_data():
    return {
        "fieldname": "buyback_quote",
        "non_standard_fieldnames": {},
        "transactions": [
            {
                "label": _("Inspection"),
                "items": ["Buyback Inspection"],
            },
            {
                "label": _("Order"),
                "items": ["Buyback Order"],
            },
        ],
    }
