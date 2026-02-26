import frappe
import csv
import io




@frappe.whitelist()
def upload_buyback_csv(file_url):

    file_doc = frappe.get_doc("File", {"file_url": file_url})
    content = file_doc.get_content()

    if not content:
        frappe.throw("Empty file")

    text = content.decode("utf-8") if isinstance(content, bytes) else content
    reader = csv.DictReader(io.StringIO(text))

    meta = frappe.get_meta("Buyback Price Master")
    field_map = {f.fieldname: f.fieldtype for f in meta.fields}

    inserted = 0
    skipped = []
    duplicates = []
    errors = []

    frappe.db.savepoint("buyback_csv")

    try:
        for idx, row in enumerate(reader, start=2):
            try:
                row = {
                    k: (v.strip() if isinstance(v, str) else v)
                    for k, v in row.items()
                }

                item_code = (row.get("item_code") or "").strip()

                if not item_code:
                    skipped.append(f"Row {idx}: Missing item_code")
                    continue

                if frappe.db.exists("Buyback Price Master", {"item_code": item_code}):
                    duplicates.append(f"Row {idx}: Duplicate item_code {item_code}")
                    continue

                doc = frappe.new_doc("Buyback Price Master")

                for key, value in row.items():
                    if key not in field_map:
                        continue

                    fieldtype = field_map[key]

                    # empty handling
                    if value in ("", None):
                        if fieldtype in ("Int", "Float", "Currency", "Percent"):
                            setattr(doc, key, 0)
                        else:
                            setattr(doc, key, None)
                        continue

                    # numeric casting
                    if fieldtype == "Int":
                        setattr(doc, key, int(float(value)))
                    elif fieldtype in ("Float", "Currency", "Percent"):
                        setattr(doc, key, float(value))
                    else:
                        setattr(doc, key, str(value))

                doc.insert(ignore_permissions=True)
                inserted += 1

            except Exception as e:
                errors.append(f"Row {idx}: {str(e)}")

    except Exception:
        frappe.db.rollback(save_point="buyback_csv")
        raise

    return {
        "message": (
            f"Upload completed. "
            f"Inserted: {inserted}, "
            f"Skipped: {len(skipped)}, "
            f"Duplicates: {len(duplicates)}, "
            f"Errors: {len(errors)}"
        ),
        "inserted": inserted,
        "skipped_rows": skipped[:20],
        "duplicate_rows": duplicates[:20],
        "error_rows": errors[:20],
    }



@frappe.whitelist()
def download_buyback_template():

    items = frappe.get_all(
        "Item",
        filters={"item_group": "Mobiles", "disabled": 0},
        fields=["item_code", "item_name"],
        ignore_permissions=True,
    )

    output = io.StringIO()
    writer = csv.writer(output)

    headers = [
        "buyback_price_id",
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
        "is_active",
    ]

    writer.writerow(headers)

    for i in items:
        writer.writerow([
            "",
            i.item_code,
            i.item_name,
            *([0] * 16),
            1,
        ])

    frappe.response.filename = "buyback_template.csv"
    frappe.response.filecontent = output.getvalue()
    frappe.response.type = "download"



@frappe.whitelist(allow_guest=True)
def get_buyback(id):

    return frappe.db.get_value(
        "Buyback Request",
        {"buybackid": id},
        "*",
        as_dict=True,
    )



@frappe.whitelist()
def confirm_deal(name):

    doc = frappe.get_doc("Buyback Request", name)

    doc.check_permission("write")

    if doc.status != "Open Request":
        return {"status": "already_processed"}

    doc.status = "Customer Approved"
    doc.save()

    return {"status": "success"}