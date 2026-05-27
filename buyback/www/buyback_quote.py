import frappe

no_cache = 1


def get_context(context):
    context.no_cache = 1
    context.prefill_mobile_no = (frappe.form_dict.get("mobile_no") or "").strip()
    context.prefill_item_code = (frappe.form_dict.get("item") or "").strip()
    context.prefill_city = (frappe.form_dict.get("city") or "").strip()
