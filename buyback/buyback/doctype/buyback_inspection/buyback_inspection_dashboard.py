from frappe import _


def get_data():
    return {
        "fieldname": "buyback_inspection",
        "non_standard_fieldnames": {},
        "transactions": [
            {
                "label": _("Order"),
                "items": ["Buyback Order"],
            },
        ],
    }
