"""
Convert the legacy ``Views.content`` HTML template dialect into the declarative
JSON ``spec`` (schema v1). Reads and writes the live ``Views`` collection.

For each document it:

* derives ``slug`` from ``filename`` (strip ``browse-`` prefix and ``.php``);
* parses ``content`` -> ``spec`` via :func:`data.table_spec.parse_view_content`;
* validates the spec (:func:`data.table_spec.validate_spec`);
* checks structural parity: ``html_signature(content) == spec_signature(spec)``.

``content`` is left untouched (removed later, after a backup, in a separate step).

Usage::

    python manage.py convert_views_to_spec --dry-run --report
    python manage.py convert_views_to_spec --only adpositions-borrowed --dry-run --report
    python manage.py convert_views_to_spec            # write spec + slug + schema_version
"""

import json
import re

from django.core.management.base import BaseCommand

from data.models import Category, View
from data.table_spec import (
    SCHEMA_VERSION,
    html_signature,
    parse_view_content,
    spec_signature,
    validate_spec,
)


def derive_slug(filename):
    s = re.sub(r"\.php$", "", (filename or "").strip(), flags=re.IGNORECASE)
    return re.sub(r"^browse-", "", s)


def _path_to_filename(path):
    f = path.strip().replace("/", "-")
    return f if f.lower().endswith(".php") else f + ".php"


def link_categories(views_by_filename):
    """Idempotently stamp ``view_slug`` on every Category whose legacy ``path``
    resolves to a live View. Returns the number of categories updated."""
    n = 0
    for c in Category.collection().all():
        path = (c.get("path") or "").strip()
        if not path:
            continue
        view = views_by_filename.get(_path_to_filename(path))
        if view and c.get("view_slug") != view.get("slug"):
            Category.collection().update({"_key": c["_key"], "view_slug": view["slug"]})
            n += 1
    return n


class Command(BaseCommand):
    help = "Convert legacy Views.content HTML into the JSON table spec (schema v1)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Do not write.")
        parser.add_argument("--report", action="store_true", help="Per-view PASS/FAIL with diffs.")
        parser.add_argument("--report-file", help="Write the full JSON report to this path.")
        parser.add_argument("--only", help="Restrict to one view by slug, filename or _key.")
        parser.add_argument("--limit", type=int, help="Process at most N views.")
        parser.add_argument(
            "--overwrite", action="store_true", help="Re-process views that already have a spec."
        )

    def handle(self, *args, **opts):
        dry_run = opts["dry_run"]
        report = opts["report"]
        only = opts["only"]
        limit = opts["limit"]
        overwrite = opts["overwrite"]

        docs = list(View.collection().all())
        if only:
            docs = [
                d
                for d in docs
                if only in (d.get("_key"), d.get("filename"), derive_slug(d.get("filename") or ""))
            ]
        if limit:
            docs = docs[:limit]

        results = []
        n_pass = n_fail = n_written = n_skipped = 0

        for d in docs:
            slug = derive_slug(d.get("filename") or "")
            entry = {"filename": d.get("filename"), "_key": d.get("_key"), "slug": slug}

            if d.get("spec") and not overwrite:
                entry["status"] = "skipped (has spec)"
                n_skipped += 1
                results.append(entry)
                continue

            try:
                spec = parse_view_content(d.get("content") or "")
                validate_spec(spec)
            except Exception as exc:  # noqa: BLE001 - want every failure captured, not raised
                entry["status"] = "ERROR"
                entry["error"] = f"{type(exc).__name__}: {exc}"
                n_fail += 1
                results.append(entry)
                continue

            hsig = html_signature(d.get("content") or "")
            ssig = spec_signature(spec)
            if hsig == ssig:
                entry["status"] = "PASS"
                n_pass += 1
            else:
                entry["status"] = "MISMATCH"
                entry["html_sig"] = hsig
                entry["spec_sig"] = ssig
                n_fail += 1

            entry["spec"] = spec

            if not dry_run and entry["status"] == "PASS":
                View.collection().update(
                    {"_key": d["_key"], "slug": slug, "spec": spec, "schema_version": SCHEMA_VERSION}
                )
                n_written += 1

            results.append(entry)

        # --- output --------------------------------------------------------
        if opts["report_file"]:
            with open(opts["report_file"], "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=2, ensure_ascii=False, default=repr)
            self.stdout.write(f"report -> {opts['report_file']}")

        if report:
            for e in results:
                if e["status"] in ("PASS", "skipped (has spec)"):
                    continue
                self.stdout.write("")
                self.stdout.write(self.style.WARNING(f"{e['status']}  {e['filename']}"))
                if "error" in e:
                    self.stdout.write(f"  {e['error']}")
                if "html_sig" in e:
                    self._diff_sig(e["html_sig"], e["spec_sig"])

        n_linked = 0
        if not dry_run and not only:
            views_by_filename = {v["filename"]: v for v in View.collection().all()}
            n_linked = link_categories(views_by_filename)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(results)} views | PASS {n_pass} | FAIL {n_fail} | "
                f"skipped {n_skipped} | written {n_written} | categories linked {n_linked}"
                + ("  (dry-run)" if dry_run else "")
            )
        )

    def _diff_sig(self, a, b):
        """Shallow first-divergence report between two structural signatures."""
        ta, sa = a
        tb, sb = b
        if ta != tb:
            self.stdout.write(f"  title: {ta!r} != {tb!r}")
        for i, (seca, secb) in enumerate(zip(sa, sb)):
            if seca == secb:
                continue
            ha, tabsa = seca
            hb, tabsb = secb
            if ha != hb:
                self.stdout.write(f"  section[{i}] heading: {ha!r} != {hb!r}")
            for j, (tba, tbb) in enumerate(zip(tabsa, tabsb)):
                if tba == tbb:
                    continue
                self.stdout.write(f"  section[{i}].table[{j}]:")
                self.stdout.write(f"    html: {tba}")
                self.stdout.write(f"    spec: {tbb}")
        if len(sa) != len(sb):
            self.stdout.write(f"  section count: {len(sa)} != {len(sb)}")
