"""
Back up answer-level L2 language names and report conflicts with the sample.

Additive and idempotent: copies every language found on an answer (see
``data.origin_languages``) into ``legacy_origin_language`` as
``{attribute_path: language}``. Nothing is removed or rewritten; this is the
safety net before the planned Origin unification drops answer-level languages.

``--conflicts-csv`` writes the answers whose language does not appear in the
sample's ``contact_languages`` at all (or whose sample has none), for review
with the data owner.

Usage:
    python manage.py backup_origin_languages --dry-run --conflicts-csv out.csv
    python manage.py backup_origin_languages
"""

import csv
from collections import Counter

from django.core.management.base import BaseCommand

from data.models import Answer
from data.origin_languages import classify, extract_origin_languages, sample_language_levels

BACKUP_FIELD = "legacy_origin_language"
CSV_STATUSES = ("not_in_sample", "sample_has_no_contact_languages")


FORM_FIELDS = ("form", "marker", "answer", "inflection", "example")


def _form(answer, path):
    """The word form the language belongs to: the marker itself for a marker path."""
    if path.startswith("markers["):
        marker = answer["markers"][int(path[len("markers["):path.index("]")])]
        return marker.get("marker") or ""
    return next((answer[f] for f in FORM_FIELDS if answer.get(f)), "")


class Command(BaseCommand):
    help = "Copy answer-level origin languages into legacy_origin_language; report sample conflicts."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report without writing.")
        parser.add_argument("--conflicts-csv", help="Write answers whose language the sample lacks.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        db = Answer.db()
        answers = db.collection(Answer.collection_name)

        samples = {
            s["ref"]: s
            for s in db.aql.execute(
                "FOR s IN Samples RETURN {ref: s.sample_ref, cl: s.contact_languages,"
                " country: s.country_code, dialect: s.dialect_name}"
            )
        }
        categories = {
            c["id"]: c["path"]
            for c in db.aql.execute(
                "FOR c IN Categories RETURN {id: c.id, path: CONCAT_SEPARATOR(' > ', SLICE(c.hierarchy, 1))}"
            )
        }

        statuses = Counter()
        conflicts = []
        updates = []
        unchanged = 0

        cursor = db.aql.execute(
            "FOR a IN Answers FILTER a.language != null OR a.origin != null"
            " OR a.base_origin != null OR a.markers != null RETURN a",
            batch_size=5000,
            stream=True,
        )
        for answer in cursor:
            found = extract_origin_languages(answer)
            if not found:
                continue

            backup = {path: language for path, _, language in found}
            if answer.get(BACKUP_FIELD) == backup:
                unchanged += 1
            else:
                updates.append({"_key": answer["_key"], BACKUP_FIELD: backup})

            sample = samples.get(answer.get("sample")) or {}
            sample_levels = sample_language_levels(sample.get("cl"))
            for path, level, language in found:
                status = classify(language, level, sample_levels)
                statuses[status] += 1
                if status in CSV_STATUSES:
                    conflicts.append({
                        "status": status,
                        "sample": answer.get("sample"),
                        "country": sample.get("country"),
                        "dialect": sample.get("dialect"),
                        "question_id": answer.get("question_id"),
                        "category": categories.get(answer.get("question_id"), ""),
                        "form": _form(answer, path),
                        "origin_level": level,
                        "answer_language": language,
                        "stored_at": path,
                        "sample_contact_languages": "; ".join(
                            f"{e.get('language')} ({e.get('source')})"
                            for e in (sample.get("cl") or []) if isinstance(e, dict)
                        ),
                        "answer_key": answer["_key"],
                    })

        for status, n in sorted(statuses.items()):
            self.stdout.write(f"{status}: {n}")

        if options["conflicts_csv"]:
            conflicts.sort(key=lambda r: (r["status"], r["sample"] or "", r["question_id"] or 0))
            with open(options["conflicts_csv"], "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(conflicts[0]) if conflicts else ["status"])
                writer.writeheader()
                writer.writerows(conflicts)
            self.stdout.write(f"Wrote {len(conflicts)} rows to {options['conflicts_csv']}")

        verb = "Would update" if dry_run else "Updated"
        if not dry_run and updates:
            for i in range(0, len(updates), 1000):
                answers.update_many(updates[i:i + 1000], merge=False, silent=True)
        self.stdout.write(f"{verb} {len(updates)} answers; {unchanged} already backed up.")
