"""
Retire the ``ResearchQuestions`` collection (folded into ``Categories``).

``ResearchQuestions`` was a materialized copy of the ``is_leaf == true``
subset of ``Categories``; the app now reads research questions straight from
``Categories`` (see ``data.views._valid_question_ids``). Because the two
copies have drifted before (the "Adverbs > Local > Adverbials" mislabel),
this command refuses to drop anything until it has verified, on this
server's own DB, that the copy holds nothing ``Categories`` doesn't:

- the same set of ids as ``Categories FILTER is_leaf == true``
- the same name / parent_id / hierarchy / hierarchy_ids on every id

(``tag_ids`` / ``tag_words`` exist only on the copy — dead leftovers of the
pre-MasterPhrases tag model, read by nothing — and are deliberately not
compared.) It also makes sure ``Categories.id`` is unique and indexed, since
every research-question lookup now filters on it.

Dry run by default; ``--apply`` writes a JSON backup of the collection and
then drops it. Idempotent: once the collection is gone, a rerun only reports
that and ensures the index.

Usage:
    python manage.py drop_research_questions
    python manage.py drop_research_questions --apply [--backup PATH]
"""

import json
import os
import tempfile
from datetime import datetime, timezone

from django.core.management.base import BaseCommand, CommandError

from data.models import Category

COMPARED_FIELDS = ("name", "parent_id", "hierarchy", "hierarchy_ids")


def find_drift(research_questions, category_leaves):
    """Every difference between the ResearchQuestions copy and the
    Categories leaves it should mirror, as human-readable strings
    (empty list = identical)."""
    rq_by_id = {d["id"]: d for d in research_questions}
    cat_by_id = {d["id"]: d for d in category_leaves}
    problems = []
    for i in sorted(rq_by_id.keys() - cat_by_id.keys()):
        problems.append(f"id={i} ({rq_by_id[i].get('name')!r}) is in ResearchQuestions but not a Categories leaf")
    for i in sorted(cat_by_id.keys() - rq_by_id.keys()):
        problems.append(f"id={i} ({cat_by_id[i].get('name')!r}) is a Categories leaf missing from ResearchQuestions")
    for i in sorted(rq_by_id.keys() & cat_by_id.keys()):
        for f in COMPARED_FIELDS:
            if rq_by_id[i].get(f) != cat_by_id[i].get(f):
                problems.append(
                    f"id={i} {f}: ResearchQuestions={rq_by_id[i].get(f)!r} Categories={cat_by_id[i].get(f)!r}"
                )
    return problems


class Command(BaseCommand):
    help = "Verify ResearchQuestions is an exact copy of the Categories leaves, then drop it."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Back up and drop the collection (default: dry run).")
        parser.add_argument("--backup", help="Where to write the JSON backup (default: a dated file in the temp dir).")

    def handle(self, *args, **options):
        db = Category.db()
        q = lambda aql: list(db.aql.execute(aql))

        dupes = q("FOR c IN Categories COLLECT id = c.id WITH COUNT INTO n FILTER n > 1 RETURN id")
        if dupes:
            raise CommandError(f"Categories.id is not unique (duplicated ids: {dupes}); fix that first.")
        if options["apply"]:
            db.collection("Categories").add_persistent_index(fields=["id"], unique=True)
            self.stdout.write("Unique index on Categories.id ensured.")

        if not db.has_collection("ResearchQuestions"):
            self.stdout.write(self.style.SUCCESS("ResearchQuestions is already gone — nothing to do."))
            return

        fields = ", ".join(f"{f}: d.{f}" for f in ("id",) + COMPARED_FIELDS)
        rqs = q(f"FOR d IN ResearchQuestions RETURN {{{fields}}}")
        leaves = q(f"FOR d IN Categories FILTER d.is_leaf == true RETURN {{{fields}}}")
        self.stdout.write(f"ResearchQuestions: {len(rqs)} docs; Categories leaves: {len(leaves)}.")

        problems = find_drift(rqs, leaves)
        if problems:
            for p in problems[:50]:
                self.stdout.write(f"  {p}")
            if len(problems) > 50:
                self.stdout.write(f"  ... and {len(problems) - 50} more")
            raise CommandError(
                f"{len(problems)} difference(s) — not dropping. Reconcile Categories first "
                "(via `manage.py category_op`), then rerun."
            )
        self.stdout.write("No drift: ResearchQuestions holds nothing Categories doesn't.")

        if not options["apply"]:
            self.stdout.write("Dry run — rerun with --apply to back up and drop ResearchQuestions.")
            return

        backup = options["backup"] or os.path.join(
            tempfile.gettempdir(),
            f"ResearchQuestions_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json",
        )
        with open(backup, "w", encoding="utf-8") as fh:
            json.dump(q("FOR d IN ResearchQuestions RETURN d"), fh, ensure_ascii=False)
        self.stdout.write(f"Backed up ResearchQuestions to {backup}")

        db.delete_collection("ResearchQuestions")
        self.stdout.write(self.style.SUCCESS("Dropped ResearchQuestions."))
