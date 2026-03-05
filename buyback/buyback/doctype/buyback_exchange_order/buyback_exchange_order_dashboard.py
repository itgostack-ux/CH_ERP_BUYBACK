from frappe import _


def get_data():
    return {
        "fieldname": "buyback_order",
        "non_standard_fieldnames": {
            "Buyback Exchange Order": "buyback_order",
        },
        "transactions": [],
    }
