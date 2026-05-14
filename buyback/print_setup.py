"""Print format setup for Buyback flows."""

import frappe


BUYBACK_RECEIPT_HTML = """
<style>
  .bb-receipt { font-family: Arial, sans-serif; font-size: 12px; color: #111; }
  .bb-title { font-size: 18px; font-weight: 700; text-align: center; margin-bottom: 6px; }
  .bb-subtitle { text-align: center; color: #555; margin-bottom: 14px; }
  .bb-grid { width: 100%; border-collapse: collapse; margin-bottom: 12px; }
  .bb-grid td, .bb-grid th { border: 1px solid #222; padding: 6px 8px; vertical-align: top; }
  .bb-grid th { background: #f2f2f2; text-align: left; }
  .bb-amount { font-size: 20px; font-weight: 700; }
  .bb-small { font-size: 10px; color: #555; }
  .bb-sign { height: 52px; }
</style>

<div class="bb-receipt">
  <div class="bb-title">Buyback Receipt</div>
  <div class="bb-subtitle">Device buyback / exchange acknowledgement</div>

  <table class="bb-grid">
    <tr>
      <th width="25%">Receipt No</th><td width="25%">{{ doc.name }}</td>
      <th width="25%">Date</th><td width="25%">{{ frappe.utils.format_datetime(doc.modified) }}</td>
    </tr>
    <tr>
      <th>Customer</th><td>{{ doc.customer_name or doc.customer or "" }}</td>
      <th>Mobile</th><td>{{ doc.mobile_no or "" }}</td>
    </tr>
    <tr>
      <th>Store</th><td>{{ doc.store or "" }}</td>
      <th>Status</th><td>{{ doc.status or "" }}</td>
    </tr>
  </table>

  <table class="bb-grid">
    <tr><th colspan="4">Device Details</th></tr>
    <tr>
      <th width="25%">Item</th><td width="25%">{{ doc.item_name or doc.item or "" }}</td>
      <th width="25%">IMEI / Serial</th><td width="25%">{{ doc.imei_serial or "" }}</td>
    </tr>
    <tr>
      <th>Grade</th><td>{{ doc.condition_grade or "" }}</td>
      <th>Warranty</th><td>{{ doc.warranty_status or "" }}</td>
    </tr>
  </table>

  <table class="bb-grid">
    <tr><th colspan="4">Settlement</th></tr>
    <tr>
      <th width="25%">Settlement Type</th><td width="25%">{{ doc.settlement_type or "Buyback" }}</td>
      <th width="25%">Payout Mode</th><td width="25%">{{ doc.customer_payout_mode or "" }}</td>
    </tr>
    <tr>
      <th>Final Price</th><td class="bb-amount">{{ frappe.format(doc.final_price or 0, {"fieldtype": "Currency"}) }}</td>
      <th>Payment Status</th><td>{{ doc.payment_status or "" }}</td>
    </tr>
    {% if doc.customer_upi_id or doc.customer_bank_account_number %}
    <tr>
      <th>Payout Reference</th>
      <td colspan="3">
        {% if doc.customer_upi_id %}UPI: {{ doc.customer_upi_id }}{% endif %}
        {% if doc.customer_bank_account_number %}
          Bank: {{ doc.customer_bank_name or "" }} / {{ doc.customer_bank_ifsc or "" }} /
          ******{{ (doc.customer_bank_account_number or "")[-4:] }}
        {% endif %}
      </td>
    </tr>
    {% endif %}
  </table>

  <table class="bb-grid">
    <tr>
      <td width="50%"><b>Customer Signature</b><div class="bb-sign"></div></td>
      <td width="50%"><b>Store Executive Signature</b><div class="bb-sign"></div></td>
    </tr>
  </table>

  <div class="bb-small">
    Customer confirms that the device details and payout preference above are correct.
    This receipt is system generated from the Buyback Order record.
  </div>
</div>
""".strip()


def ensure_print_formats():
    """Ensure app-managed print formats referenced by POS exist."""
    values = {
        "doctype": "Print Format",
        "name": "Buyback Receipt",
        "doc_type": "Buyback Order",
        "module": "BuyBack",
        "print_format_type": "Jinja",
        "print_format_for": "DocType",
        "custom_format": 1,
        "standard": "No",
        "disabled": 0,
        "html": BUYBACK_RECEIPT_HTML,
        "css": "",
    }
    if frappe.db.exists("Print Format", "Buyback Receipt"):
        doc = frappe.get_doc("Print Format", "Buyback Receipt")
        changed = False
        for field, value in values.items():
            if field in ("doctype", "name"):
                continue
            if doc.get(field) != value:
                doc.set(field, value)
                changed = True
        if changed:
            doc.save(ignore_permissions=True)
    else:
        frappe.get_doc(values).insert(ignore_permissions=True)
    frappe.db.commit()