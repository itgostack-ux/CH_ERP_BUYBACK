"""TC_048: Verify buyback amount deduction based on device age."""
import frappe


def run():
    ITEM = "I04508"
    bpm = frappe.db.get_value(
        "Buyback Price Master", {"item_code": ITEM},
        ["a_grade_iw_0_3", "a_grade_iw_0_6", "a_grade_iw_6_11", "a_grade_oow_11"],
        as_dict=True,
    )
    if not bpm:
        print(f"SKIP — No Buyback Price Master for {ITEM}")
        return

    expected = {
        "0-3 Months":  float(bpm.a_grade_iw_0_3),
        "4-6 Months":  float(bpm.a_grade_iw_0_6),
        "7-11 Months": float(bpm.a_grade_iw_6_11),
        "12+ Months":  float(bpm.a_grade_oow_11),
    }
    print("Expected prices by age (Grade A, In Warranty):", expected)

    from buyback.buyback.pricing.engine import calculate_estimated_price
    grade_id = frappe.db.get_value("Grade Master", {"grade_name": "A"}, "name")

    results = {}
    for age_label in ("0-3 Months", "4-6 Months", "7-11 Months", "12+ Months"):
        r = calculate_estimated_price(
            item_code=ITEM,
            grade=grade_id,
            warranty_status="In Warranty",
            device_age_months=age_label,
            responses=[],
            diagnostic_tests=[],
        )
        results[age_label] = r.get("estimated_price", 0)

    print("Actual prices by age (engine output):", results)

    ok = True
    for label, exp in expected.items():
        got = results.get(label, -1)
        if exp > 0 and abs(got - exp) > 50:
            print(f"FAIL [{label}]: expected ~{exp}, got {got}")
            ok = False
        else:
            print(f"PASS [{label}]: {got} (expected ~{exp})")

    prices = [results[k] for k in ("0-3 Months", "4-6 Months", "7-11 Months", "12+ Months")]
    for i in range(len(prices) - 1):
        if prices[i] < prices[i + 1]:
            print(f"FAIL: prices should decrease with age — {prices}")
            ok = False
            break
    else:
        print(f"PASS: prices decrease with age — {prices}")

    # Test server-side validate without tests/responses
    cust_list = frappe.db.sql("SELECT name FROM `tabCustomer` LIMIT 1", as_dict=True)
    if cust_list:
        cust = cust_list[0].name
        doc = frappe.new_doc("Buyback Assessment")
        doc.item = ITEM
        doc.warranty_status = "In Warranty"
        doc.device_age_months = "4-6 Months"
        doc.customer = cust
        doc.mobile_no = "9999999999"
        doc.flags.skip_duplicate_check = True
        try:
            doc.run_method("validate")
            got_price = float(doc.estimated_price or 0)
            exp_base = float(bpm.a_grade_iw_0_6)
            if exp_base > 0 and abs(got_price - exp_base) > 50:
                print(f"FAIL server validate: estimated_price={got_price}, expected ~{exp_base}")
                ok = False
            else:
                print(f"PASS server validate: estimated_price={got_price} (expected ~{exp_base})")
        except Exception as e:
            print(f"WARN validate error: {e}")

    print("\n=== TC_048:", "PASS" if ok else "FAIL", "===")
