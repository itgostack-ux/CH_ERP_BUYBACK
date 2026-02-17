import frappe
import csv
import io
import re
from frappe.utils import get_url
from frappe.model.document import Document


# =====================================================
# Upload CSV
# =====================================================

@frappe.whitelist()
def upload_buyback_csv(file_url):

    file_doc = frappe.get_doc("File", {"file_url": file_url})
    content = file_doc.get_content()

    if not content:
        frappe.throw("Empty file")

    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content

    reader = csv.DictReader(io.StringIO(text))
    inserted = 0

    meta = frappe.get_meta("Buyback Price Master")
    valid_fields = [f.fieldname for f in meta.fields]

    for row in reader:

        item_code = (row.get("item_code") or "").strip()

        if not item_code:
            continue

        if frappe.db.exists("Buyback Price Master", {"item_code": item_code}):
            continue

        doc = frappe.new_doc("Buyback Price Master")

        for key, value in row.items():
            if key in valid_fields:
                setattr(doc, key, value or 0)

        doc.insert(ignore_permissions=True)
        inserted += 1

    frappe.db.commit()

    return {"message": f"Upload completed. Inserted {inserted} rows"}


# =====================================================
# Download Template
# =====================================================

@frappe.whitelist()
def download_buyback_template():

    items = frappe.get_all(
        "Item",
        filters={"item_group": "Mobiles", "disabled": 0},
        fields=["item_code", "item_name"],
        ignore_permissions=True
    )

    output = io.StringIO()
    writer = csv.writer(output)

    headers = [
        "sku_id",
        "item_code",
        "item_name",
        "current_market_price",
        "vendor_price",
        "a_grade_iw_0_3",
        "b_grade_iw_0_3",
        "c_grade_iw_0_3",
        "a_grade_iw_0_6",
        "b_grade_iw_0_6",
        "c_grade_iw_0_6",
        "d_grade_iw_0_6",
        "a_grade_iw_6_11",
        "b_grade_iw_6_11",
        "c_grade_iw_6_11",
        "d_grade_iw_6_11",
        "a_grade_oow_11",
        "b_grade_oow_11",
        "c_grade_oow_11",
        "d_grade_oow_11",
        "is_active"
    ]

    writer.writerow(headers)

    for i in items:
        writer.writerow([
            "",
            i.item_code,
            i.item_name,
            0, 0, 0, 0, 0,
            0, 0, 0, 0,
            0, 0, 0, 0,
            0, 0, 0, 0,
            1
        ])

    frappe.response.filename = "buyback_template.csv"
    frappe.response.filecontent = output.getvalue()
    frappe.response.type = "download"


# =====================================================
# Buyback APIs
# =====================================================

@frappe.whitelist(allow_guest=True)
def get_buyback(id):

    doc = frappe.db.get_value(
        "Buyback Request",
        {"buybackid": id},
        "*",
        as_dict=True
    )

    return doc


@frappe.whitelist()
def confirm_deal(name):

    doc = frappe.get_doc("Buyback Request", name)

    if doc.status != "Open Request":
        frappe.throw(f"Cannot approve. Current status: {doc.status}")

    doc.status = "Customer Approved"
    doc.save(ignore_permissions=True)

    return "OK"


# =====================================================
# Validation Logic
# =====================================================

def validate_buyback(doc):

    if not doc.customer_name:
        frappe.throw("Customer name is required")

    if not doc.mobile_no or not re.match(r"^\d{10}$", doc.mobile_no):
        frappe.throw("Mobile number must be 10 digits")

    if doc.email and "@" not in doc.email:
        frappe.throw("Invalid email address")

    if not doc.item_full_name:
        frappe.throw("Item name is required")

    if not doc.imei:
        frappe.throw("IMEI is required")

    existing = frappe.db.exists(
        "Buyback Request",
        {"imei": doc.imei, "name": ["!=", doc.name]}
    )

    if existing:
        frappe.throw("This IMEI already exists")

    # --------------------------
    # Deal / No Deal validation
    # --------------------------

    if doc.deal_status == "No Deal":
        if not doc.no_deal_reason:
            frappe.throw("Reason required for No Deal")

    if doc.deal_status == "Deal":
        if doc.buyback_price <= 0:
            frappe.throw("Price required when deal is approved")

    # --------------------------
    # Payment validation
    # --------------------------

    if doc.payment_mode_name == "Bank Transfer":

        if not doc.bank_name:
            frappe.throw("Bank name required")

        if not doc.account_holder_name:
            frappe.throw("Account holder name required")

        if not doc.account_no:
            frappe.throw("Account number required")

        if not doc.ifsc_code:
            frappe.throw("IFSC code required")

    # --------------------------
    # Status validation
    # --------------------------

    allowed_status = [
        "Open Request",
        "Customer Approved",
        "Finance Approved",
        "Payment Done",
        "Completed",
        "Rejected"
    ]

    if doc.status not in allowed_status:
        frappe.throw("Invalid status selected")


# =====================================================
# Buyback ID Generator
# =====================================================

def generate_buyback_id(doc):

    last = frappe.db.sql("""
        SELECT MAX(buybackid)
        FROM `tabBuyback Request`
        FOR UPDATE
    """)[0][0] or 0

    doc.buybackid = last + 1


# =====================================================
# Buyback Request Class
# =====================================================

class BuybackRequest(Document):

    def before_insert(self):
        generate_buyback_id(self)

    def validate(self):
        validate_buyback(self)

    def after_insert(self):
        send_approval_email(self)


# =====================================================
# Email Approval Automation
# =====================================================

def send_approval_email(doc):

    base_url = get_url()
    approval_link = f"{base_url}/approval?id={doc.buybackid}"

    message = f"""
    <h3>Buyback Approval Required</h3>

    <p>Please review and approve the request:</p>

    <p>
        <a href="{approval_link}"
           style="background:#28a745;
                  color:white;
                  padding:12px 20px;
                  text-decoration:none;
                  border-radius:6px;">
            Open Approval Page
        </a>
    </p>

    <p>Request ID: {doc.buybackid}</p>
    """

    frappe.sendmail(
        recipients=[doc.email],
        subject="Buyback Approval Required",
        message=message
    )
