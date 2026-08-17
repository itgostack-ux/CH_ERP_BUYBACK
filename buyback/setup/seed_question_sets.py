"""Seed the question catalogue and the three device question sets.

Idempotent. Re-running rewrites question text, purpose, fault code and options
to match `question_catalogue`, so the catalogue module is the source of truth
and hand-edits in Desk are overwritten on the next migrate. Set membership is
rewritten the same way.

Run:  bench --site <site> execute buyback.setup.seed_question_sets.run
"""

import frappe

from buyback.setup.question_catalogue import QUESTIONS, SETS, validate_catalogue


def _upsert_question(code: str, spec: dict) -> str:
    name = frappe.db.get_value("Buyback Question Bank", {"question_code": code}, "name")
    doc = (frappe.get_doc("Buyback Question Bank", name) if name
           else frappe.new_doc("Buyback Question Bank"))

    doc.question_code = code
    doc.question_text = spec["text"]
    doc.question_purpose = spec["purpose"]
    doc.fault_code = spec.get("fault_code")
    doc.question_type = "Single Select"
    # Everything lands in one list on the assessment. Splitting the same
    # device across an "Automated Test" table and a "Customer Question" table
    # is what let one fault be asked — and charged — twice.
    doc.diagnosis_type = "Customer Question"
    doc.disabled = 0
    doc.is_mandatory = 1

    if spec.get("category") and frappe.db.exists("Buyback Question Category", spec["category"]):
        doc.question_category = spec["category"]

    doc.set("options", [])
    for value, label, forces_grade, percent in spec["options"]:
        doc.append("options", {
            "option_value": value,
            "option_label": label,
            "forces_grade": forces_grade or "",
            # Stored as a positive magnitude; the engine takes abs() either way.
            "price_impact_percent": percent,
        })

    doc.flags.ignore_permissions = True
    doc.save(ignore_permissions=True)
    return doc.name


def _upsert_set(spec: dict, question_names: dict[str, str]) -> str:
    name = frappe.db.get_value(
        "Buyback Question Set", {"set_name": spec["set_name"]}, "name"
    )
    doc = (frappe.get_doc("Buyback Question Set", name) if name
           else frappe.new_doc("Buyback Question Set"))

    doc.set_name = spec["set_name"]
    doc.applies_to_brand_family = spec["brand_family"]
    doc.applies_to_form_factor = spec["form_factor"]
    doc.description = spec["description"]
    doc.disabled = 0

    doc.set("questions", [])
    for position, code in enumerate(spec["questions"], start=1):
        doc.append("questions", {
            "question": question_names[code],
            "display_order": position * 10,
        })

    doc.save(ignore_permissions=True)
    return doc.name


def run(retire_legacy: int = 0):
    """Seed catalogue + sets.

    Args:
        retire_legacy: when truthy, disable every Question Bank row that the
            catalogue does not define. Off by default — retiring the old bank
            changes what inspectors are asked, so it is an explicit decision
            rather than a side effect of a migrate.
    """
    validate_catalogue()

    question_names = {code: _upsert_question(code, spec) for code, spec in QUESTIONS.items()}
    print(f"✔ {len(question_names)} questions upserted")

    for spec in SETS:
        set_name = _upsert_set(spec, question_names)
        print(f"✔ {set_name}: {len(spec['questions'])} questions")

    retired = 0
    if int(retire_legacy or 0):
        keep = set(QUESTIONS)
        for row in frappe.get_all(
            "Buyback Question Bank", filters={"disabled": 0}, fields=["name", "question_code"]
        ):
            if row.question_code not in keep:
                frappe.db.set_value(
                    "Buyback Question Bank", row.name, "disabled", 1, update_modified=False
                )
                retired += 1
        print(f"✔ {retired} legacy questions disabled")

    frappe.db.commit()
    return {"questions": len(question_names), "sets": len(SETS), "retired": retired}
