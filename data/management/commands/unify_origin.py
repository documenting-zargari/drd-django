"""
Convert Origin data to the unified format (see ``data.origin_languages``).

* Answers: one ``origin`` level string (Inherited | Current-L2 | Recent-L2 |
  Old-L2); ``base_origin`` and ``markers[i].origin`` likewise. ``source``,
  ``language``, ``preposition_origin`` and object/"Level: Language" variants
  are removed.
* Samples: ``contact_languages[].source`` renamed to ``level``.
* Views: Origin columns in table specs point at the plain fields.

Idempotent; run on each server's own DB. Answers are skipped (and listed) when
their languages aren't backed up in ``legacy_origin_language`` yet (run
``backup_origin_languages`` first) or when they can't be converted without
losing information (e.g. two different levels).

Usage:
    python manage.py unify_origin --dry-run
    python manage.py unify_origin
"""

from collections import Counter

from django.core.management.base import BaseCommand

from data.models import Answer
from data.origin_languages import extract_origin_languages, unify_answer, unify_contact_languages

BACKUP_FIELD = "legacy_origin_language"

# Table-spec field strings -> unified field.
SPEC_FIELD_MAP = {
    "source|language": "origin",
    "source|language|origin": "origin",
    "origin.source|origin.language": "origin",
    "preposition_origin": "origin",
    "base_origin.source|base_origin.language": "base_origin",
    "markers.origin.source|markers.origin.language": "markers.origin",
}


def rewrite_spec(node):
    """Rewrite Origin ``field`` bindings in a table spec in place; return the count."""
    count = 0
    if isinstance(node, dict):
        if node.get("field") in SPEC_FIELD_MAP:
            node["field"] = SPEC_FIELD_MAP[node["field"]]
            count += 1
        for value in node.values():
            count += rewrite_spec(value)
    elif isinstance(node, list):
        for value in node:
            count += rewrite_spec(value)
    return count


def is_backed_up(answer):
    backup = {path: language for path, _, language in extract_origin_languages(answer)}
    return not backup or all(answer.get(BACKUP_FIELD, {}).get(p) == l for p, l in backup.items())


class Command(BaseCommand):
    help = "Unify answer Origin storage, rename sample contact_languages source->level, update table specs."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report without writing.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        verb = "Would update" if dry_run else "Updated"
        db = Answer.db()

        # Answers
        updates, skipped = [], []
        unchanged = 0
        cursor = db.aql.execute(
            "FOR a IN Answers FILTER a.source != null OR a.language != null OR a.origin != null"
            " OR a.preposition_origin != null OR a.base_origin != null OR a.markers != null RETURN a",
            batch_size=5000,
            stream=True,
        )
        for answer in cursor:
            if not is_backed_up(answer):
                skipped.append((answer, "language not backed up"))
                continue
            patch, problem = unify_answer(answer)
            if problem:
                skipped.append((answer, problem))
            elif patch:
                updates.append({"_key": answer["_key"], **patch})
            else:
                unchanged += 1
        if not dry_run:
            collection = db.collection(Answer.collection_name)
            for i in range(0, len(updates), 1000):
                collection.update_many(updates[i:i + 1000], keep_none=False, merge=False, silent=True)
        self.stdout.write(f"Answers: {verb.lower()} {len(updates)}, {unchanged} already unified, {len(skipped)} skipped.")
        for problem, n in Counter(p for _, p in skipped).most_common():
            self.stdout.write(f"  skipped ({problem}): {n}")
        for answer, problem in skipped:
            self.stdout.write(
                f"    {answer['_key']} q{answer.get('question_id')} {answer.get('sample')}"
                f" {answer.get('form')!r}: {problem}"
            )

        # Samples
        sample_updates = []
        for sample in db.aql.execute("FOR s IN Samples FILTER IS_ARRAY(s.contact_languages) RETURN s"):
            unified = unify_contact_languages(sample["contact_languages"])
            if unified != sample["contact_languages"]:
                sample_updates.append({"_key": sample["_key"], "contact_languages": unified})
        if not dry_run and sample_updates:
            db.collection("Samples").update_many(sample_updates, merge=False, silent=True)
        self.stdout.write(f"Samples: {verb.lower()} {len(sample_updates)} contact_languages.")

        # Views
        view_updates, bindings = [], 0
        for view in db.aql.execute("FOR v IN Views FILTER v.spec != null RETURN {_key: v._key, spec: v.spec}"):
            n = rewrite_spec(view["spec"])
            if n:
                bindings += n
                view_updates.append(view)
        if not dry_run and view_updates:
            db.collection("Views").update_many(view_updates, merge=False, silent=True)
        self.stdout.write(f"Views: {verb.lower()} {len(view_updates)} specs ({bindings} Origin bindings).")
