"""
One-off, idempotent re-keying of the ``Translations`` collection.

Background
----------
``Translations`` holds the per-language translations of each phrase's English
gloss. The current API (``GET/PATCH /master-phrases/{phrase_ref}/translations/``
and the ``PhraseTranslation`` model) assumes the document ``_key`` **is** the
``phrase_ref`` — it does a single ``collection.get(phrase_ref)`` lookup.

But the rows imported from the legacy database still carry their old identity:
an opaque numeric ``_key`` (``297655068``…), an ``anchor_id`` and an
``anchor_english`` left over from the removed ``PhraseAnchors`` / ``TranslatesTo``
edge model — and **no** ``phrase_ref``. So every translations lookup misses and
the UI shows "no translations" for phrases that actually have a dozen.

Mapping
-------
``anchor_english`` matches ``MasterPhrases.english`` exactly, and for every
distinct english string the number of ``Translations`` rows equals the number of
``MasterPhrases`` rows (verified: 1095 == 1095, zero unmatched). So:

* unique english  -> assign that phrase_ref
* duplicated english (15 strings, 31 rows) -> zip the group deterministically:
  translations ordered by ``anchor_id``, phrase_refs ordered numerically then
  lexically.

Each legacy row is rewritten as ``{_key: phrase_ref, phrase_ref, translations}``
(Arango keys are immutable, so this is insert-new + delete-old) and the anchor
cruft is dropped.

Safe to run repeatedly: a row whose ``_key`` is already a real ``phrase_ref`` is
only touched to backfill the ``phrase_ref`` field if missing; nothing is
re-keyed twice, and an existing hand-edited ``Translations/{phrase_ref}`` doc is
never clobbered.

Usage:
    python manage.py migrate_translation_keys --dry-run
    python manage.py migrate_translation_keys
"""

from collections import defaultdict

from django.core.management.base import BaseCommand

from data.models import MasterPhrase


def _phrase_ref_sort_key(ref: str):
    """Numeric refs ("1", "2", "10") before alpha ("355e"), each in order."""
    return (0, int(ref), "") if ref.isdigit() else (1, 0, ref)


class Command(BaseCommand):
    help = "Re-key the Translations collection by phrase_ref so the translations API can find rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        db = MasterPhrase.db()
        translations = db.collection("Translations")

        master_refs = {
            row["ref"]
            for row in db.aql.execute("FOR m IN MasterPhrases RETURN {ref: m._key}")
        }
        mp_by_english = defaultdict(list)
        for row in db.aql.execute(
            "FOR m IN MasterPhrases RETURN {ref: m._key, en: m.english}"
        ):
            mp_by_english[(row["en"] or "").strip()].append(row["ref"])
        for refs in mp_by_english.values():
            refs.sort(key=_phrase_ref_sort_key)

        rows = list(
            db.aql.execute(
                "FOR t IN Translations RETURN {"
                "  key: t._key, phrase_ref: t.phrase_ref,"
                "  anchor_english: t.anchor_english, anchor_id: t.anchor_id,"
                "  translations: t.translations }"
            )
        )

        # Legacy rows still keyed by their old opaque id, grouped by the english
        # gloss they belong to. Rows already keyed by a real phrase_ref are
        # handled separately (backfill only).
        legacy_by_english = defaultdict(list)
        already_keyed = []
        for row in rows:
            if row["key"] in master_refs:
                already_keyed.append(row)
            else:
                legacy_by_english[(row["anchor_english"] or "").strip()].append(row)
        for group in legacy_by_english.values():
            group.sort(key=lambda r: (r["anchor_id"] is None, r["anchor_id"], r["key"]))

        rekeyed = 0
        backfilled = 0
        skipped_existing = 0
        unresolved = []

        # 1. Backfill phrase_ref on rows that are already correctly keyed.
        for row in already_keyed:
            if row["phrase_ref"] == row["key"]:
                continue
            self.stdout.write(f"  backfill phrase_ref on Translations/{row['key']}")
            if not dry_run:
                translations.update({"_key": row["key"], "phrase_ref": row["key"]})
            backfilled += 1

        # 2. Re-key legacy rows.
        for english, group in sorted(legacy_by_english.items()):
            candidate_refs = mp_by_english.get(english)
            if not candidate_refs or len(candidate_refs) != len(group):
                unresolved.extend(row["key"] for row in group)
                continue

            for row, ref in zip(group, candidate_refs):
                if ref in master_refs and translations.get(ref):
                    # A real translations doc for this phrase_ref already
                    # exists (hand-edited via the API) — never overwrite it.
                    self.stdout.write(
                        self.style.WARNING(
                            f"  skip {row['key']}: Translations/{ref} already exists"
                        )
                    )
                    skipped_existing += 1
                    continue

                self.stdout.write(
                    f"  {row['key']} -> {ref}  ({english!r}, "
                    f"{len(row['translations'] or [])} languages)"
                )
                if not dry_run:
                    translations.insert(
                        {
                            "_key": ref,
                            "phrase_ref": ref,
                            "translations": row["translations"] or [],
                        }
                    )
                    translations.delete(row["key"])
                rekeyed += 1

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"{'[dry-run] ' if dry_run else ''}"
                f"rekeyed={rekeyed} phrase_ref_backfilled={backfilled} "
                f"skipped_existing={skipped_existing}"
            )
        )
        if unresolved:
            self.stdout.write(
                self.style.WARNING(
                    f"unresolved (english count mismatch): {', '.join(unresolved)}"
                )
            )
