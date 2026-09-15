"""
Per-collection translators: turn a legacy-schema diff (added/removed/changed
_keys, resolved against the baseline and current dump directories) into
writes against the refactored schema.

Each translator has the signature:

    translate(added, removed, changed, baseline_docs, current_docs, db, dry_run, stdout) -> dict

`baseline_docs`/`current_docs` are {_key: doc} for *this* collection, loaded
from the baseline/current dump directories. Returns a small dict of counters
for the summary report. Every translator must be idempotent — re-running
after a partial or previous apply should not double-write or error.
"""

from data.legacy_replay import normalize_doc


def _get(db, collection, key):
    try:
        return db.collection(collection).get(key)
    except Exception:
        return None


def translate_phrases(added, removed, changed, baseline_docs, current_docs, db, dry_run, stdout):
    """Phrases (legacy, keyed by opaque _key) -> SamplePhrases (keyed by
    '{sample}_{phrase_ref}'). The legacy english/conjugated/tag_ids fields
    are dropped — that information now lives on MasterPhrases, addressed by
    phrase_ref, not duplicated per sample.
    """
    sample_phrases = db.collection("SamplePhrases")
    master_phrases = db.collection("MasterPhrases")
    counts = {"deleted": 0, "upserted": 0, "unresolved_phrase_ref": 0, "skipped_no_sample_ref": 0}

    for key in removed:
        doc = baseline_docs[key]
        sample, ref = doc.get("sample"), doc.get("phrase_ref")
        if not sample or not ref:
            counts["skipped_no_sample_ref"] += 1
            continue
        sp_key = f"{sample}_{ref}"
        if _get(db, "SamplePhrases", sp_key) is not None:
            counts["deleted"] += 1
            if not dry_run:
                sample_phrases.delete(sp_key, ignore_missing=True)
            else:
                stdout.write(f"    [dry-run] would delete SamplePhrases/{sp_key}")

    for key in list(added) + list(changed):
        doc = current_docs[key]
        sample, ref = doc.get("sample"), doc.get("phrase_ref")
        if not sample or not ref:
            counts["skipped_no_sample_ref"] += 1
            continue
        if _get(db, "MasterPhrases", ref) is None:
            counts["unresolved_phrase_ref"] += 1
            stdout.write(
                f"    WARNING: Phrases/{key} (sample={sample}, phrase_ref={ref}) has no "
                f"matching MasterPhrases/{ref} — not linked to any research question. Skipped."
            )
            continue
        sp_key = f"{sample}_{ref}"
        new_doc = {
            "_key": sp_key,
            "sample": sample,
            "phrase_ref": ref,
            "phrase": doc.get("phrase"),
            "has_recording": bool(doc.get("has_recording")),
        }
        existing = _get(db, "SamplePhrases", sp_key)
        if existing is not None:
            # Preserve any question_overrides a sample editor already set.
            if "question_overrides" in existing:
                new_doc["question_overrides"] = existing["question_overrides"]
            if normalize_doc({**existing, **new_doc}) == normalize_doc(existing):
                continue  # already up to date
        counts["upserted"] += 1
        if not dry_run:
            if existing is not None:
                sample_phrases.update(new_doc)
            else:
                sample_phrases.insert(new_doc)
        else:
            stdout.write(f"    [dry-run] would upsert SamplePhrases/{sp_key}: {new_doc}")

    return counts


# Legacy-only fields that never made it into the new Answers schema; see
# extract/master_phrases_migration/cleanup_answer_tags.py.
_ANSWER_LEGACY_FIELDS = {"tags", "tag"}


def translate_answers(added, removed, changed, baseline_docs, current_docs, db, dry_run, stdout, force_conflicts=False):
    """Answers keeps the same _key space and field names across both schema
    eras, minus the legacy tag(s) field the refactor already stripped
    locally. Inserts new answers; for 'changed' rows, only applies the
    diff that remains after ignoring the legacy fields (so re-running after
    the one-time tags cleanup is a no-op)."""
    answers = db.collection("Answers")
    counts = {"inserted": 0, "conflicts": 0, "removed_reported": len(removed)}

    for key in added:
        doc = current_docs[key]
        new_doc = {k: v for k, v in doc.items() if k not in ("_rev", "_id") and k not in _ANSWER_LEGACY_FIELDS}
        if _get(db, "Answers", key) is not None:
            continue  # already inserted by a previous run
        counts["inserted"] += 1
        if not dry_run:
            answers.insert(new_doc)
        else:
            stdout.write(f"    [dry-run] would insert Answers/{key} (sample={doc.get('sample')})")

    for key in changed:
        old, new = baseline_docs[key], current_docs[key]
        old_stripped = {k: v for k, v in old.items() if k not in ("_rev", "_id") and k not in _ANSWER_LEGACY_FIELDS}
        new_stripped = {k: v for k, v in new.items() if k not in ("_rev", "_id") and k not in _ANSWER_LEGACY_FIELDS}
        if normalize_doc(old_stripped) == normalize_doc(new_stripped):
            continue  # production's content didn't actually move beyond the stripped legacy fields —
            # nothing to apply, and critically: skip BEFORE the network round-trip below. This is the
            # vast majority of "changed" Answers (the tags-cleanup noise); checking the live target for
            # each one first (as an earlier version of this code did) meant one HTTPS round-trip per key
            # — ~215,000 of them against a remote target, which is what actually burned 39 minutes for a
            # replay that should take under a minute. Filter locally first; only hit the network for a key
            # where production's content genuinely differs from baseline.
        existing = _get(db, "Answers", key)
        if existing is None:
            continue  # nothing to update; report separately if this is unexpected
        # Compare against the TARGET's current live value, not the stale
        # Jul-13 baseline: several migration steps (tags cleanup, the
        # phrase_overrides/transcription_overrides backfill) were re-applied
        # directly to production too, independently of local dev. Diffing
        # against baseline would flag those as "changed" even though both
        # sides already agree — only a genuine mismatch against what's live
        # right now is worth reporting.
        merged = {**existing, **new_stripped}
        if normalize_doc(merged) == normalize_doc(existing):
            continue  # target already matches; the baseline diff was migration-step noise
        counts["conflicts"] += 1
        if force_conflicts:
            counts["conflicts_resolved_production"] = counts.get("conflicts_resolved_production", 0) + 1
            if not dry_run:
                answers.update({**new_stripped, "_key": key})
            stdout.write(f"    RESOLVED (production wins) Answers/{key}{' [dry-run]' if dry_run else ''}: {new_stripped}")
        else:
            stdout.write(
                f"    CONFLICT Answers/{key}: production and the live target disagree — not auto-applied.\n"
                f"      production : {new_stripped}\n"
                f"      live target: {existing}"
            )

    if removed:
        stdout.write(
            f"    NOTE: {len(removed)} Answers removed on production are not auto-deleted here "
            f"— review and delete manually if confirmed intentional."
        )

    return counts


def translate_translations(added, removed, changed, baseline_docs, current_docs, db, dry_run, stdout, anchor_to_ref=None):
    """Translations, re-keyed exactly like migrate_translation_keys.py:
    anchor_id -> phrase_ref via the baseline PhraseAnchors mapping."""
    translations = db.collection("Translations")
    counts = {"upserted": 0, "unresolved_anchor": 0}
    anchor_to_ref = anchor_to_ref or {}

    for key in list(added) + list(changed):
        doc = current_docs[key]
        ref = doc.get("phrase_ref") or anchor_to_ref.get(doc.get("anchor_id"))
        if not ref:
            counts["unresolved_anchor"] += 1
            stdout.write(f"    WARNING: Translations/{key} (anchor_id={doc.get('anchor_id')}) has no phrase_ref mapping. Skipped.")
            continue
        new_doc = {"_key": ref, "phrase_ref": ref, "translations": doc.get("translations")}
        existing = _get(db, "Translations", ref)
        if existing is not None and normalize_doc({**existing, **new_doc}) == normalize_doc(existing):
            continue
        counts["upserted"] += 1
        if not dry_run:
            if existing is not None:
                translations.update(new_doc)
            else:
                translations.insert(new_doc)
        else:
            stdout.write(f"    [dry-run] would upsert Translations/{ref}")

    return counts


def translate_direct(collection_name):
    """Factory for DIRECT_PASSTHROUGH collections: same _key space and
    schema in both eras, so add/update/delete by _key with no translation."""

    def translate(added, removed, changed, baseline_docs, current_docs, db, dry_run, stdout, force_conflicts=False):
        coll = db.collection(collection_name)
        counts = {"inserted": 0, "conflicts": 0, "deleted": 0}

        for key in added:
            doc = {k: v for k, v in current_docs[key].items() if k not in ("_rev", "_id")}
            if _get(db, collection_name, key) is not None:
                continue
            counts["inserted"] += 1
            if not dry_run:
                coll.insert(doc)
            else:
                stdout.write(f"    [dry-run] would insert {collection_name}/{key}")

        for key in changed:
            # Never auto-overwrite: some fields in this "same schema" bucket
            # (e.g. Transcriptions.question_ids) are independently recomputed
            # by backfill scripts on both sides, not user-edited — production's
            # copy can be a stale/narrower snapshot of the same computation
            # (verified: a live target had MORE question_ids than production's
            # dump for the same transcription). A real editorial change (e.g.
            # a Samples field) looks identical to that at the diff level, so
            # every 'changed' doc here is surfaced for review, never applied
            # automatically — same policy as the Answers translator.
            doc = {k: v for k, v in current_docs[key].items() if k not in ("_rev", "_id")}
            existing = _get(db, collection_name, key)
            if existing is None:
                continue
            if normalize_doc({**existing, **doc}) == normalize_doc(existing):
                continue  # target already matches; baseline diff was noise
            counts["conflicts"] += 1
            if force_conflicts:
                counts["conflicts_resolved_production"] = counts.get("conflicts_resolved_production", 0) + 1
                if not dry_run:
                    coll.update(doc)
                stdout.write(f"    RESOLVED (production wins) {collection_name}/{key}{' [dry-run]' if dry_run else ''}: {doc}")
            else:
                stdout.write(
                    f"    CONFLICT {collection_name}/{key}: production and the live target disagree — not auto-applied.\n"
                    f"      production : {doc}\n"
                    f"      live target: {existing}"
                )

        for key in removed:
            if _get(db, collection_name, key) is None:
                continue
            counts["deleted"] += 1
            if not dry_run:
                coll.delete(key, ignore_missing=True)
            else:
                stdout.write(f"    [dry-run] would delete {collection_name}/{key}")

        return counts

    return translate


TRANSLATORS = {
    "Phrases": translate_phrases,
    "Answers": translate_answers,
    "Translations": translate_translations,
}
