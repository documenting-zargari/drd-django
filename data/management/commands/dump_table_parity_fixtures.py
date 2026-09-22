"""
Build the real-data corpus for the client-side golden render-parity test
(see the Tables view-spec refactor plan). For every live ``View`` doc that
has a converted ``spec``, this picks one real ``Sample`` with populated
``Answer`` docs for every question id the spec references, and emits
everything the parity test needs to render both the old (``content``) and
new (``spec``) pipelines against the same answer data:

    {slug, content, spec, sampleRef, answersByQuestion: {questionId: [answer, ...]}}

Read-only - writes one JSON file, touches nothing in Arango.

Usage:
    python manage.py dump_table_parity_fixtures
    python manage.py dump_table_parity_fixtures --out /path/to/fixtures.json
"""

import json
from datetime import datetime, timezone

from django.core.management.base import BaseCommand

from data.models import View


def _collect_question_ids(spec):
    """Every questionId a spec references, in the order it's first seen."""
    seen = []
    for section in spec.get("sections", []):
        for table in section.get("tables", []):
            if table.get("kind") == "template":
                for row in table.get("rows", []):
                    qid = row.get("questionId")
                    if isinstance(qid, int) and qid not in seen:
                        seen.append(qid)
            else:
                for row in table.get("rows", []):
                    for cell in row.get("cells", []) or []:
                        qid = (cell or {}).get("questionId")
                        if isinstance(qid, int) and qid not in seen:
                            seen.append(qid)
    return seen


class Command(BaseCommand):
    help = "Dump a real spec+content+answers fixture corpus for the Tables render-parity test."

    def add_arguments(self, parser):
        parser.add_argument(
            "--out",
            help="Output file path. Defaults to "
                 "../roma-client/src/app/tables/testing/parity-fixtures.generated.json",
        )

    def handle(self, *args, **options):
        db = View.db()
        docs = [d for d in View.collection().all() if d.get("spec")]

        fixtures = []
        skipped = []
        for doc in docs:
            slug = doc.get("slug")
            spec = doc["spec"]
            question_ids = _collect_question_ids(spec)
            if not question_ids:
                skipped.append((slug, "no questionIds in spec"))
                continue

            # Pick the sample with the most answers among the referenced
            # question ids - real views commonly have far more question ids
            # than any single sample answers (optional/rare fields), so we
            # can't require full coverage; best coverage still exercises
            # every construct the spec uses (real answers > synthetic ones).
            sample_rows = list(db.aql.execute(
                """
                FOR a IN Answers
                    FILTER a.question_id IN @ids
                    COLLECT sample = a.sample WITH COUNT INTO n
                    SORT n DESC
                    LIMIT 1
                    RETURN {sample, n}
                """,
                bind_vars={"ids": question_ids},
            ))
            if not sample_rows:
                skipped.append((slug, f"no sample has any answer for these {len(question_ids)} question ids"))
                continue
            sample_ref = sample_rows[0]["sample"]
            coverage = sample_rows[0]["n"]

            answers = list(db.aql.execute(
                "FOR a IN Answers FILTER a.question_id IN @ids AND a.sample == @sample RETURN a",
                bind_vars={"ids": question_ids, "sample": sample_ref},
            ))
            answers_by_question = {}
            for a in answers:
                answers_by_question.setdefault(str(a["question_id"]), []).append(a)

            fixtures.append({
                "slug": slug,
                "content": doc.get("content") or "",
                "spec": spec,
                "sampleRef": sample_ref,
                "coverage": f"{coverage}/{len(question_ids)}",
                "answersByQuestion": answers_by_question,
            })

        out_path = options["out"] or (
            "../roma-client/src/app/tables/testing/parity-fixtures.generated.json"
        )
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(fixtures),
            "skipped": skipped,
            "fixtures": fixtures,
        }
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)

        self.stdout.write(self.style.SUCCESS(
            f"Wrote {len(fixtures)} fixtures -> {out_path} ({len(skipped)} views skipped)"
        ))
        for slug, reason in skipped:
            self.stdout.write(self.style.WARNING(f"  skipped {slug}: {reason}"))
