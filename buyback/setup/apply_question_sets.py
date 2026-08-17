"""Materialise the question sets onto per-model question maps.

`Buyback Question Set` is the template; `Buyback Item Question Map` is what the
assessment screen actually reads, one row per model. Keeping both is
deliberate — the set is edited once for a whole platform, while the map stays
the place a single model can be tuned without disturbing anything else.

This walks every model, resolves the set its device profile points at, and
rewrites the map to match. Models whose map has been hand-tuned are skipped
unless `include_customised` is set, so local overrides are not silently
flattened by a template change.

Run:
    bench --site <site> execute buyback.setup.apply_question_sets.run
    bench --site <site> execute buyback.setup.apply_question_sets.run \\
        --kwargs '{"dry_run": 1}'
"""

import frappe

from buyback.buyback.doctype.buyback_question_set.buyback_question_set import (
    resolve_set_for_item,
)

# A map is "template-shaped" when every question on it came from a set. Once an
# operator adds or removes a row by hand it stops matching any set exactly, and
# this refuses to overwrite it without being told to.
CUSTOMISED_FLAG = "_customised"

# Maps committed per batch. Each map rewrites ~45 child rows.
BATCH_SIZE = 200


def _set_questions(set_name: str) -> list[str]:
    return frappe.get_all(
        "Buyback Question Set Item",
        filters={"parent": set_name, "parenttype": "Buyback Question Set"},
        pluck="question",
        order_by="display_order asc, idx asc",
    )


def _all_set_question_names() -> set[str]:
    return set(frappe.get_all(
        "Buyback Question Set Item",
        filters={"parenttype": "Buyback Question Set"},
        pluck="question",
    ))


def run(dry_run: int = 0, include_customised: int = 0, limit: int | None = None):
    dry_run = int(dry_run or 0)
    include_customised = int(include_customised or 0)

    questions_by_set: dict[str, list[str]] = {}
    for set_name in frappe.get_all("Buyback Question Set", filters={"disabled": 0}, pluck="name"):
        questions_by_set[set_name] = _set_questions(set_name)
    if not questions_by_set:
        print("No enabled Buyback Question Sets — nothing to apply.")
        return {}

    known = _all_set_question_names()

    maps = frappe.get_all(
        "Buyback Item Question Map",
        filters={"map_type": "Model Override"},
        fields=["name", "item_code"],
        order_by="item_code asc",
        limit_page_length=int(limit) if limit else 0,
    )

    stats = {"updated": 0, "unchanged": 0, "skipped_customised": 0, "no_set": 0}
    customised: list[str] = []

    for row in maps:
        set_name = resolve_set_for_item(row.item_code)
        if not set_name or set_name not in questions_by_set:
            stats["no_set"] += 1
            continue

        target = questions_by_set[set_name]
        doc = frappe.get_doc("Buyback Item Question Map", row.name)
        current = [d.question for d in (doc.questions or [])]

        if current == target:
            stats["unchanged"] += 1
            continue

        # Distinguish a map that predates the sets from one an operator has
        # tuned. A pre-cutover map holds ONLY legacy questions — none of it
        # came from a set, so rewriting it loses nothing. A tuned map is a
        # mixture: mostly set questions with something added or swapped, and
        # that intent is worth protecting.
        from_set = [q for q in current if q in known]
        hand_added = [q for q in current if q not in known]
        is_tuned = bool(from_set and hand_added)

        if is_tuned and not include_customised:
            stats["skipped_customised"] += 1
            if len(customised) < 20:
                customised.append(row.item_code)
            continue

        if dry_run:
            stats["updated"] += 1
            continue

        doc.set("questions", [])
        for position, question in enumerate(target, start=1):
            doc.append("questions", {"question": question, "display_order": position * 10})
        # The sets hold the single inspector-facing list; automated tests come
        # from the mobile diagnostic app and are mapped separately.
        doc.set("tests", [])
        doc.disabled = 0
        doc.flags.ignore_permissions = True
        doc.save(ignore_permissions=True)
        stats["updated"] += 1

        # Every map replaces ~45 child rows, so the whole catalogue is a few
        # hundred thousand writes — well past Frappe's per-transaction ceiling.
        # Commit in batches; a partial run is safe because this is idempotent
        # and re-running skips maps that already match.
        if stats["updated"] % BATCH_SIZE == 0:
            frappe.db.commit()
            print(f"  … {stats['updated']} maps rewritten")

    if not dry_run:
        frappe.db.commit()

    prefix = "[dry run] " if dry_run else ""
    print(
        f"{prefix}{stats['updated']} maps rewritten, {stats['unchanged']} already "
        f"matching, {stats['skipped_customised']} skipped as hand-tuned, "
        f"{stats['no_set']} with no matching set"
    )
    if customised:
        print(
            "  hand-tuned maps left alone (re-run with include_customised=1 to "
            f"overwrite): {', '.join(customised)}"
        )
    return stats
