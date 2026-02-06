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

    reader = csv.reader(io.StringIO(text))
    header = True
    inserted = 0

    for row in reader:

        if header:
            header = False
            continue

        if len(row) < 4:
            continue

        item_code = row[0].strip()
        item_name = row[1].strip()
        price = row[2] or 0
        vendor = row[3] or 0

        # skip duplicates
        if frappe.db.exists("Buyback Price Master", {"item_code": item_code}):
            continue

        doc = frappe.new_doc("Buyback Price Master")
        doc.item_code = item_code
        doc.item_name = item_name
        doc.current_market__price = price
        doc.vendor_price = vendor
        doc.insert(ignore_permissions=True)

        inserted += 1

    return {"message": f"Upload completed. Inserted {inserted} rows"}


# ---------------------------
# Download Template
# ---------------------------
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

    writer.writerow([
        "item_code",
        "item_name",
        "current_market_price",
        "vendor_price"
    ])

    for i in items:
        writer.writerow([i.item_code, i.item_name, 0, 0])

    frappe.response.filename = "buyback_template.csv"
    frappe.response.filecontent = output.getvalue()
    frappe.response.type = "download"
