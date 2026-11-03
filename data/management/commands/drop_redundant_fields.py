"""
Unset document fields that duplicate information held elsewhere.

- ``Categories.hierarchy`` / ``hierarchy_ids``: every node's full ancestor
  chain, now computed from ``parent_id`` by ``data.category_tree``. The dry
  run lists any node whose stored chain differs from the computed one (i.e.
  what the API served before vs. what it serves now) — informational only,
  since the computed chain is already what the app uses.
- ``Answers`` legacy labels (``LEGACY_ANSWER_FIELDS``): per-source-table
  values carried over by extract/manual_extraction (e.g. ``category_id`` is
  the *MySQL* category id of the source table, not a Categories id). Each is
  fully determined by ``question_id`` and read by nothing; the dry run
  re-checks that, and refuses to drop a field for which it no longer holds.

Run ``drop_research_questions`` first: its drift check compares the stored
hierarchy arrays, so this refuses while ``ResearchQuestions`` still exists.

Dry run by default; ``--apply`` writes a JSON backup of every value it will
remove ({collection: {_key: {field: value}}}) and then unsets them.
Idempotent: a rerun finds nothing left to drop.

Usage:
    python manage.py drop_redundant_fields
    python manage.py drop_redundant_fields --apply [--backup PATH]
"""

import json
import os
import tempfile
from datetime import datetime, timezone

from django.core.management.base import BaseCommand, CommandError

from data.category_tree import CategoryTree
from data.models import Category

HIERARCHY_FIELDS = ("hierarchy", "hierarchy_ids")
LEGACY_ANSWER_FIELDS = ("category_id", "category", "question", "question_name", "category_name", "subcategory")


def find_hierarchy_mismatches(categories):
    """Nodes whose stored hierarchy arrays differ from the ones computed
    from parent_id, as human-readable strings. Nodes without stored arrays
    are skipped."""
    tree = CategoryTree(categories)
    problems = []
    for doc in sorted(categories, key=lambda d: d["id"]):
        for field, computed in (("hierarchy", tree.hierarchy(doc["id"])), ("hierarchy_ids", tree.hierarchy_ids(doc["id"]))):
            if doc.get(field) is not None and doc[field] != computed:
                problems.append(f"id={doc['id']} {field}: stored={doc[field]!r} computed={computed!r}")
    return problems


class Command(BaseCommand):
    help = "Unset Categories.hierarchy/hierarchy_ids and the legacy per-table labels on Answers."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Back up and unset the fields (default: dry run).")
        parser.add_argument("--backup", help="Where to write the JSON backup (default: a dated file in the temp dir).")

    def handle(self, *args, **options):
        db = Category.db()
        q = lambda aql, **bind: list(db.aql.execute(aql, bind_vars=bind))

        if db.has_collection("ResearchQuestions"):
            raise CommandError("ResearchQuestions still exists — run `manage.py drop_research_questions` first.")

        # --- Categories ------------------------------------------------------
        categories = q(
            "FOR c IN Categories RETURN {_key: c._key, id: c.id, name: c.name, parent_id: c.parent_id,"
            " hierarchy: c.hierarchy, hierarchy_ids: c.hierarchy_ids}"
        )
        stored = [c for c in categories if any(c.get(f) is not None for f in HIERARCHY_FIELDS)]
        self.stdout.write(f"Categories: {len(stored)} of {len(categories)} docs still store hierarchy arrays.")
        mismatches = find_hierarchy_mismatches(categories)
        if mismatches:
            self.stdout.write(f"  {len(mismatches)} stored value(s) differ from the computed hierarchy (now served):")
            for p in mismatches[:20]:
                self.stdout.write(f"    {p}")
            if len(mismatches) > 20:
                self.stdout.write(f"    ... and {len(mismatches) - 20} more")

        # --- Answers ---------------------------------------------------------
        answer_fields = []
        for field in LEGACY_ANSWER_FIELDS:
            count, ambiguous = q(
                """
                LET per_question = (
                    FOR a IN Answers FILTER HAS(a, @f)
                    COLLECT qid = a.question_id INTO g = a[@f]
                    RETURN {n: LENGTH(g), values: LENGTH(UNIQUE(g))}
                )
                RETURN [SUM(per_question[*].n), LENGTH(per_question[* FILTER CURRENT.values > 1])]
                """,
                f=field,
            )[0]
            if not count:
                continue
            if ambiguous:
                raise CommandError(
                    f"Answers.{field}: {ambiguous} question(s) carry more than one value — it is not "
                    "redundant with question_id there; investigate before dropping."
                )
            self.stdout.write(f"Answers.{field}: {count} docs (one value per question_id).")
            answer_fields.append(field)

        if not stored and not answer_fields:
            self.stdout.write(self.style.SUCCESS("Nothing left to drop."))
            return
        if not options["apply"]:
            self.stdout.write("Dry run — rerun with --apply to back up and unset these fields.")
            return

        backup_path = options["backup"] or os.path.join(
            tempfile.gettempdir(),
            f"redundant_fields_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json",
        )
        backup = {
            "Categories": {c["_key"]: {f: c.get(f) for f in HIERARCHY_FIELDS} for c in stored},
            "Answers": {
                a.pop("_key"): a
                for a in q(
                    "FOR a IN Answers FILTER LENGTH(ATTRIBUTES(a)[* FILTER CURRENT IN @fields]) > 0"
                    " RETURN MERGE({_key: a._key}, KEEP(a, @fields))",
                    fields=answer_fields,
                )
            },
        }
        with open(backup_path, "w", encoding="utf-8") as fh:
            json.dump(backup, fh, ensure_ascii=False)
        self.stdout.write(f"Backed up {len(backup['Categories'])} Categories and {len(backup['Answers'])} Answers docs to {backup_path}")

        # REPLACE, not UPDATE: an update merges, so it can't remove attributes.
        q(
            "FOR c IN Categories FILTER HAS(c, 'hierarchy') OR HAS(c, 'hierarchy_ids')"
            " REPLACE c WITH UNSET(c, @fields) IN Categories",
            fields=list(HIERARCHY_FIELDS),
        )
        if answer_fields:
            q(
                "FOR a IN Answers FILTER LENGTH(ATTRIBUTES(a)[* FILTER CURRENT IN @fields]) > 0"
                " REPLACE a WITH UNSET(a, @fields) IN Answers",
                fields=answer_fields,
            )
        self.stdout.write(self.style.SUCCESS(
            f"Unset {', '.join(HIERARCHY_FIELDS)} on Categories"
            + (f" and {', '.join(answer_fields)} on Answers." if answer_fields else ".")
        ))
