"""
Read-only audit of the live ``Views`` collection (Phase 0 / Discovery for the
table-spec refactor).

The ``Views`` documents hold ``{filename, content, parent_id}`` where ``content``
is an HTML string in a homegrown PHP-era template dialect (``[foreach]`` row
templates, ``data-rowspan`` merge attributes, "JAML" cell payloads such as
``[{id: 267, field: "form"}]``). Before that dialect can be replaced with a
declarative JSON ``spec`` we need an exact description of what is *actually* in
the live collection - not what the legacy ``extract/`` staging files contain.

This command connects to ArangoDB through the normal Django-configured client,
walks every ``Views`` document, and reports:

* per-document: ``_key``, ``filename``, derived ``slug``, ``parent_id``,
  whether a ``spec`` already exists, ``content`` length;
* structural constructs in use: ``<table>`` count per view, ``<h1>/<h2>/<h3>``
  usage, ``[foreach]`` blocks vs plain ``<tr>`` rows, ``colspan`` values,
  ``data-rowspan`` values, nested ``<table>`` inside a ``[foreach]`` block,
  header-only tables (no data rows);
* every distinct JAML payload shape: the set of ``field`` names, which use pipe
  compounds (``source|language``), which carry ``tableField`` (and its path
  shapes), any ``rowspan: true``, and any payload the tolerant reader cannot
  classify;
* ``slug`` collisions after stripping ``browse-`` / ``.php``;
* cross-check against ``Categories``: which categories carry a ``path`` and
  whether it resolves to a live view (orphans in both directions).

It writes nothing. ``--json`` emits the machine-readable report; the default is
a human summary.

Usage:
    python manage.py audit_views
    python manage.py audit_views --json > views_audit.json
    python manage.py audit_views --dump-payloads   # list every distinct JAML payload verbatim
"""

import json
import re
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand

from data.models import Category, View

# --- dialect recognisers (ported from roma-client tables.component.ts) --------

# A <table> whose body carries a repeated row template.
FOREACH_ROW_RE = re.compile(r"\[foreach\]\s*<tr", re.IGNORECASE)
ENDFOREACH_ROW_RE = re.compile(r"</tr>\s*\[endforeach\]", re.IGNORECASE)
FOREACH_BLOCK_RE = re.compile(r"\[foreach\](.*?)\[endforeach\]", re.IGNORECASE | re.DOTALL)

TABLE_RE = re.compile(r"<table\b[^>]*>(.*?)</table>", re.IGNORECASE | re.DOTALL)
TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
H2_RE = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)
H3_RE = re.compile(r"<h3\b[^>]*>(.*?)</h3>", re.IGNORECASE | re.DOTALL)

COLSPAN_RE = re.compile(r"colspan\s*=\s*\"?(\d+)\"?", re.IGNORECASE)
DATA_ROWSPAN_RE = re.compile(r"data-rowspan\s*=\s*\"?([a-z0-9]+)\"?", re.IGNORECASE)
ROWSPAN_ATTR_RE = re.compile(r"(?<!data-)rowspan\s*=\s*\"?(\d+)\"?", re.IGNORECASE)

# JAML: the client treats any {...} containing both `id` and `field` as a payload.
JAML_PAYLOAD_RE = re.compile(r"\{[^{}]*\bid\b[^{}]*:[^{}]*\bfield\b[^{}]*\}", re.IGNORECASE)
JAML_ID_RE = re.compile(r"\bid\s*:\s*\"?([^,}\"\s]+)", re.IGNORECASE)
JAML_FIELD_RE = re.compile(r"\bfield\s*:\s*\"?([^,}\"]+?)\"?\s*(?:[,}])", re.IGNORECASE)
JAML_TABLEFIELD_RE = re.compile(r"\btableField\s*:\s*\"?([^,}\"]+?)\"?\s*(?:[,}])", re.IGNORECASE)
JAML_ROWSPAN_TRUE_RE = re.compile(r"\browspan\s*:\s*true\b", re.IGNORECASE)

TAG_RE = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub("", fragment or "")).strip()


def derive_slug(filename: str) -> str:
    s = (filename or "").strip()
    s = re.sub(r"\.php$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^browse-", "", s)
    return s


def classify_payload(raw: str) -> dict:
    """Best-effort structured view of one ``[{...}]`` JAML payload."""
    field_m = JAML_FIELD_RE.search(raw)
    id_m = JAML_ID_RE.search(raw)
    tf_m = JAML_TABLEFIELD_RE.search(raw)
    field = field_m.group(1).strip() if field_m else None
    return {
        "raw": raw.strip(),
        "id": id_m.group(1).strip() if id_m else None,
        "field": field,
        "field_is_compound": bool(field and "|" in field),
        "field_parts": field.split("|") if field else [],
        "tableField": tf_m.group(1).strip() if tf_m else None,
        "tableField_is_compound": bool(tf_m and "|" in tf_m.group(1)),
        "has_rowspan_true": bool(JAML_ROWSPAN_TRUE_RE.search(raw)),
        "unclassified": not (id_m and field_m),
    }


def audit_one(doc: dict) -> dict:
    content = doc.get("content") or ""
    tables = TABLE_RE.findall(content)

    per_table = []
    nested_foreach_table = 0
    header_only_tables = 0
    for tbody in tables:
        foreach_blocks = FOREACH_BLOCK_RE.findall(tbody)
        # nested <table> living inside a [foreach] ... [endforeach]
        for blk in foreach_blocks:
            if TABLE_RE.search(blk):
                nested_foreach_table += 1
        rows = TR_RE.findall(tbody)
        rows_with_jaml = sum(1 for r in rows if JAML_PAYLOAD_RE.search(r))
        if rows and rows_with_jaml == 0:
            header_only_tables += 1
        per_table.append(
            {
                "rows": len(rows),
                "foreach_blocks": len(foreach_blocks),
                "rows_with_jaml": rows_with_jaml,
                "has_foreach_rows": bool(
                    FOREACH_ROW_RE.search(tbody) and ENDFOREACH_ROW_RE.search(tbody)
                ),
            }
        )

    payloads = [classify_payload(p) for p in JAML_PAYLOAD_RE.findall(content)]

    return {
        "_key": doc.get("_key"),
        "filename": doc.get("filename"),
        "slug": derive_slug(doc.get("filename") or ""),
        "parent_id": doc.get("parent_id"),
        "has_spec": "spec" in doc and doc.get("spec") not in (None, {}, ""),
        "content_len": len(content),
        "extra_fields": sorted(
            k
            for k in doc.keys()
            if k not in {"_key", "_id", "_rev", "filename", "content", "parent_id"}
        ),
        "h1": [_text(x) for x in H1_RE.findall(content)],
        "h2_count": len(H2_RE.findall(content)),
        "h3_count": len(H3_RE.findall(content)),
        "table_count": len(tables),
        "per_table": per_table,
        "nested_foreach_table": nested_foreach_table,
        "header_only_tables": header_only_tables,
        "colspan_values": sorted({int(x) for x in COLSPAN_RE.findall(content)}),
        "data_rowspan_values": sorted({x.lower() for x in DATA_ROWSPAN_RE.findall(content)}),
        "rowspan_attr_values": sorted({int(x) for x in ROWSPAN_ATTR_RE.findall(content)}),
        "payload_count": len(payloads),
        "payloads": payloads,
    }


class Command(BaseCommand):
    help = "Read-only audit of the live Views collection (table-spec refactor, Phase 0)."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit the full machine-readable report as JSON.")
        parser.add_argument(
            "--dump-payloads",
            action="store_true",
            help="In the human summary, list every distinct JAML payload verbatim.",
        )

    def handle(self, *args, **options):
        as_json = options["json"]
        dump_payloads = options["dump_payloads"]

        view_docs = list(View.collection().all())
        audits = [audit_one(d) for d in view_docs]

        # --- slug collisions ---------------------------------------------------
        slug_map = defaultdict(list)
        for a in audits:
            slug_map[a["slug"]].append(a["filename"])
        slug_collisions = {s: fns for s, fns in slug_map.items() if len(fns) > 1}

        # --- category <-> view cross-check -----------------------------------
        cat_docs = list(Category.collection().all())
        cats_with_path = [
            {"id": c.get("id"), "name": c.get("name"), "path": c.get("path")}
            for c in cat_docs
            if (c.get("path") or "").strip()
        ]
        view_filenames = {a["filename"] for a in audits}
        # client maps category.path "browse/foo/bar.php" -> filename "browse-foo-bar.php"
        def path_to_filename(p: str) -> str:
            p = p.strip()
            f = p.replace("/", "-")
            if not f.lower().endswith(".php"):
                f = f + ".php"
            return f

        cat_path_targets = {path_to_filename(c["path"]): c for c in cats_with_path}
        orphan_categories = sorted(
            f"{c['name']} (id={c['id']}, path={c['path']})"
            for tgt, c in cat_path_targets.items()
            if tgt not in view_filenames
        )
        views_without_category = sorted(
            a["filename"] for a in audits if a["filename"] not in cat_path_targets
        )

        # --- aggregates -----------------------------------------------------
        all_payloads = [p for a in audits for p in a["payloads"]]
        distinct_payload_raw = sorted({p["raw"] for p in all_payloads})
        field_names = Counter(p["field"] for p in all_payloads if p["field"])
        compound_fields = sorted({p["field"] for p in all_payloads if p["field_is_compound"]})
        tablefield_shapes = sorted({p["tableField"] for p in all_payloads if p["tableField"]})
        rowspan_true_payloads = [p["raw"] for p in all_payloads if p["has_rowspan_true"]]
        unclassified_payloads = sorted({p["raw"] for p in all_payloads if p["unclassified"]})

        colspan_values = sorted({v for a in audits for v in a["colspan_values"]})
        data_rowspan_values = sorted({v for a in audits for v in a["data_rowspan_values"]})
        rowspan_attr_values = sorted({v for a in audits for v in a["rowspan_attr_values"]})

        multi_table_views = [a for a in audits if a["table_count"] > 1]
        section_views = [a for a in audits if a["h2_count"] or a["h3_count"]]
        nested_views = [a for a in audits if a["nested_foreach_table"]]
        header_only_views = [
            a for a in audits if a["header_only_tables"] and a["header_only_tables"] == a["table_count"]
        ]
        plain_row_views = [
            a
            for a in audits
            if any(t["rows_with_jaml"] and not t["has_foreach_rows"] for t in a["per_table"])
        ]
        already_converted = [a for a in audits if a["has_spec"]]
        extra_field_names = Counter(f for a in audits for f in a["extra_fields"])

        report = {
            "n_views": len(audits),
            "n_categories": len(cat_docs),
            "already_converted": [a["filename"] for a in already_converted],
            "extra_field_names": dict(extra_field_names),
            "slug_collisions": slug_collisions,
            "multi_table_views": [
                {"filename": a["filename"], "table_count": a["table_count"]} for a in multi_table_views
            ],
            "section_views": [
                {"filename": a["filename"], "h2": a["h2_count"], "h3": a["h3_count"]}
                for a in section_views
            ],
            "nested_foreach_table_views": [a["filename"] for a in nested_views],
            "header_only_views": [a["filename"] for a in header_only_views],
            "plain_row_views": [a["filename"] for a in plain_row_views],
            "colspan_values": colspan_values,
            "data_rowspan_values": data_rowspan_values,
            "rowspan_attr_values": rowspan_attr_values,
            "distinct_payload_count": len(distinct_payload_raw),
            "field_names": dict(field_names.most_common()),
            "compound_fields": compound_fields,
            "tablefield_shapes": tablefield_shapes,
            "rowspan_true_payloads": rowspan_true_payloads,
            "unclassified_payloads": unclassified_payloads,
            "category_path_orphans": orphan_categories,
            "views_without_category_path": views_without_category,
            "per_view": audits,
            "distinct_payloads": distinct_payload_raw,
        }

        if as_json:
            self.stdout.write(json.dumps(report, indent=2, ensure_ascii=False))
            return

        w = self.stdout.write
        w("")
        w(self.style.MIGRATE_HEADING("Views collection audit"))
        w(f"  views:                {report['n_views']}")
        w(f"  categories:           {report['n_categories']}")
        w(f"  already have `spec`:  {len(already_converted)}")
        if extra_field_names:
            w(f"  non-standard doc fields: {dict(extra_field_names)}")
        w("")
        w(self.style.MIGRATE_HEADING("Structure"))
        w(f"  multi-<table> views:  {len(multi_table_views)}")
        w(f"  views with <h2>/<h3>: {len(section_views)}")
        w(f"  plain-row views:      {len(plain_row_views)}  (non-[foreach] rows carrying JAML)")
        w(f"  header-only views:    {len(header_only_views)}  (no data rows at all)")
        w(f"  nested-table-in-foreach views: {len(nested_views)}  {[a['filename'] for a in nested_views]}")
        w(f"  colspan values seen:      {colspan_values}")
        w(f"  data-rowspan values seen: {data_rowspan_values}")
        w(f"  numeric rowspan= seen:    {rowspan_attr_values}")
        w("")
        w(self.style.MIGRATE_HEADING("JAML payloads"))
        w(f"  total payloads:       {len(all_payloads)}")
        w(f"  distinct payloads:    {len(distinct_payload_raw)}")
        w(f"  distinct field names: {len(field_names)}")
        w(f"    {dict(field_names.most_common())}")
        w(f"  compound (piped) fields:  {compound_fields}")
        w(f"  tableField shapes:        {tablefield_shapes}")
        w(f"  payloads with rowspan:true: {len(rowspan_true_payloads)}  {rowspan_true_payloads[:5]}")
        if unclassified_payloads:
            w(self.style.WARNING(f"  UNCLASSIFIED payloads ({len(unclassified_payloads)}):"))
            for p in unclassified_payloads:
                w(f"    {p}")
        else:
            w(self.style.SUCCESS("  all payloads classified (id + field found)"))
        w("")
        w(self.style.MIGRATE_HEADING("Slugs"))
        if slug_collisions:
            w(self.style.WARNING(f"  {len(slug_collisions)} collision(s):"))
            for s, fns in slug_collisions.items():
                w(f"    {s}: {fns}")
        else:
            w(self.style.SUCCESS("  no slug collisions after stripping browse-/.php"))
        w("")
        w(self.style.MIGRATE_HEADING("Category <-> View linkage"))
        w(f"  categories with a `path`:          {len(cats_with_path)}")
        w(f"  ...whose path has no live view:    {len(orphan_categories)}")
        for o in orphan_categories:
            w(f"      {o}")
        w(f"  views not referenced by any category path: {len(views_without_category)}")
        for v in views_without_category:
            w(f"      {v}")
        w("")
        if dump_payloads:
            w(self.style.MIGRATE_HEADING("All distinct JAML payloads"))
            for p in distinct_payload_raw:
                w(f"  {p}")
            w("")
        w(self.style.SUCCESS("audit complete (no writes performed)"))
