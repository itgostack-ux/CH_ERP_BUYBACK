import frappe
from frappe import _
from frappe.model.document import Document


class BuybackQuestionSet(Document):
    def validate(self):
        self._reject_repeated_questions()
        self._reject_repeated_faults()
        self._renumber()

    def _reject_repeated_questions(self):
        """One question, one row. The old bank asked the same thing twice."""
        seen = {}
        for row in self.questions:
            if row.question in seen:
                frappe.throw(
                    _("{0} appears twice in this set (rows {1} and {2}). "
                      "Each question belongs in a set once.").format(
                        frappe.bold(row.question_text or row.question),
                        seen[row.question], row.idx,
                    ),
                    title=_("Repeated Question"),
                )
            seen[row.question] = row.idx

    def _reject_repeated_faults(self):
        """Two different questions may not cover the same fault either.

        This is the check that would have caught "Speaker" sitting alongside
        "Speaker Faulty" — distinct questions, one defect, charged twice.
        """
        if not self.questions:
            return
        rows = frappe.get_all(
            "Buyback Question Bank",
            filters={"name": ["in", [r.question for r in self.questions]]},
            fields=["name", "question_text", "fault_code"],
        )
        by_fault: dict[str, list[str]] = {}
        for row in rows:
            code = (row.fault_code or "").strip()
            if code:
                by_fault.setdefault(code, []).append(row.question_text or row.name)

        clashes = {code: texts for code, texts in by_fault.items() if len(texts) > 1}
        if clashes:
            detail = "<br>".join(
                f"<b>{code}</b>: {', '.join(texts)}" for code, texts in sorted(clashes.items())
            )
            frappe.throw(
                _("These questions describe the same fault, so the customer would be "
                  "charged twice for it. Keep one of each:<br><br>{0}").format(detail),
                title=_("Duplicate Fault In Set"),
            )

    def _renumber(self):
        for position, row in enumerate(self.questions, start=1):
            if not row.display_order:
                row.display_order = position * 10


def resolve_set_for_item(item_code: str) -> str | None:
    """Pick the question set for a device, most specific match first.

    Specificity is (brand family, form factor): a set naming both beats one
    naming a single dimension, which beats a catch-all. Apple has no foldable
    yet, so an Apple foldable would fall through to the Apple standard set
    rather than the Android foldable one.
    """
    family, form_factor = get_device_profile(item_code)

    candidates = frappe.get_all(
        "Buyback Question Set",
        filters={"disabled": 0},
        fields=["name", "applies_to_brand_family", "applies_to_form_factor"],
    )

    def score(row):
        if row.applies_to_brand_family not in ("Any", family):
            return None
        if row.applies_to_form_factor not in ("Any", form_factor):
            return None
        return ((row.applies_to_brand_family != "Any")
                + (row.applies_to_form_factor != "Any"))

    ranked = sorted(
        ((score(row), row.name) for row in candidates),
        key=lambda pair: (pair[0] is not None, pair[0] or 0),
        reverse=True,
    )
    for points, name in ranked:
        if points is not None:
            return name
    return None


def get_device_profile(item_code: str) -> tuple[str, str]:
    """Return (brand_family, form_factor) for an item.

    Brand family comes from the sub-category rather than the brand list: the
    site already splits "Smart Phones-iOS Phones" from "Smart Phones-Android
    Phones", and that split is maintained by the catalogue team. Brand is the
    fallback for items filed outside those sub-categories.
    """
    item = frappe.db.get_value(
        "Item", item_code, ["brand", "ch_sub_category", "ch_is_foldable"], as_dict=True
    ) or frappe._dict()

    sub_category = (item.get("ch_sub_category") or "").lower()
    if "ios" in sub_category:
        family = "Apple"
    elif "android" in sub_category:
        family = "Android"
    else:
        family = "Apple" if (item.get("brand") or "") == "Apple" else "Android"

    form_factor = "Foldable" if item.get("ch_is_foldable") else "Standard"
    return family, form_factor
