import frappe

@frappe.whitelist()
def upload_buyback_csv(file_url):

    file_doc = frappe.get_doc("File", {"file_url": file_url})
    content = file_doc.get_content()

    if not content:
        frappe.throw("Empty file")

    if isinstance(content, str):
        text = content
    else:
        text = content.decode("utf-8")

    lines = text.split("\n")
    header = True

    for line in lines:

        if header:
            header = False
            continue

        if not line.strip():
            continue

        parts = line.split(",")

        if len(parts) < 4:
            continue

        doc = frappe.new_doc("Buyback Price Master")
        doc.item_code = parts[0].strip()
        doc.item_name = parts[1].strip()
        doc.current_market__price = parts[2] or 0
        doc.vendor_price = parts[3] or 0

        doc.insert(ignore_permissions=True)

    return {"message": "Upload completed"}
