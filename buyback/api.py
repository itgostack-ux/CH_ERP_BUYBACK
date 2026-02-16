import frappe
import csv
import io


# ---------------------------
# Upload CSV
# ---------------------------

@frappe.whitelist()
def upload_buyback_csv(file_url):

    file_doc = frappe.get_doc("File", {"file_url": file_url})
    content = file_doc.get_content()

    if not content:
        frappe.throw("Empty file")

    text = content.decode("utf-8") if isinstance(content, bytes) else content

    reader = csv.DictReader(io.StringIO(text))
    inserted = 0

    # get valid doctype fields
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


# ---------------------------
# Download Template
# ---------------------------
import frappe
import csv
import io

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

    # default values row
    for i in items:
        row = [
            "",  # sku_id auto
            i.item_code,
            i.item_name,
            0, 0, 0, 0, 0,
            0, 0, 0, 0,
            0, 0, 0, 0,
            0, 0, 0, 0,1
        ]
        writer.writerow(row)

    frappe.response.filename = "buyback_template.csv"
    frappe.response.filecontent = output.getvalue()
    frappe.response.type = "download"
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

    # Only allow approval from Open Request
    if doc.status != "Open Request":
        frappe.throw(f"Cannot approve. Current status: {doc.status}")

    doc.status = "Customer Approved"
    doc.save(ignore_permissions=True)

    return "OK"
