"""
The declarative table ``spec`` that replaces the PHP-era HTML template dialect
stored in ``Views.content``.

Two public jobs:

* :func:`validate_spec` - structural validation, used by the write endpoint and
  by the converter as a post-conversion self-check.
* :func:`parse_view_content` - one-way conversion of a legacy ``content`` HTML
  string into a ``spec`` dict. Faithful port of the parsing rules in the Angular
  client (``roma-client/src/app/tables/tables.component.ts``), which is the
  executable definition of the dialect.

Plus :func:`html_signature` / :func:`spec_signature` - reduced structural
fingerprints compared by the converter to prove old and new agree.

Schema (v1)::

    { schemaVersion: 1,
      title: str,
      sections: [ { heading: str|None, tables: [ Table ] } ] }

    Table = {
      kind: "template" | "grid" | "list",
      caption: str|None,
      rowHeaderWidth: int >= 0,
      columnHeader: [[ HeaderCell ]],      # header rows, verbatim (corner + data cols); [] if none
      columns: [ { cell: CellBinding|None } ],
      multiAnswer: "rows" | "combine",     # template only; converter always emits "rows"
      rows: [ Row ],
    }
    HeaderCell = { label: str, colspan?: int, rowspan?: int }
    Row (template) = { labels: [str], questionId: int }
    Row (grid/list) = { labels: [str], cells: [ CellBinding|None ] }
    CellBinding = { field: str, questionId?: int, layout: "inline" | "stack",
                    filter?: {str: str} }

``field`` is a ``|``-separated list of ``.``-separated key paths. Resolution
(done in the client renderer, not here): walk the dots into the answer; if a
segment lands on a list, map the remainder over each element; join the ``|``
parts of one value with ``": "``. ``layout:"stack"`` renders one value per line,
``"inline"`` (default) comma-joins. This subsumes the old ``tableField`` key and
the ``[foreach]<div>`` cell wrapper.

``filter`` (optional): when a questionId has more than one Answer document
(e.g. one recording an Adjective form, another an Adverb form of the same
research question), restrict resolution to the answer(s) whose fields match
every ``{key: value}`` pair given. Done in the client renderer.
"""

import html
import re
from html.parser import HTMLParser

from rest_framework import serializers

SCHEMA_VERSION = 1

_KIND_VALUES = {"template", "grid", "list"}
_LAYOUT_VALUES = {"inline", "stack"}
_MULTI_VALUES = {"rows", "combine"}

# field path: pipe-separated list of dot-separated lowercase keys.
_FIELD_RE = re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_]+)*(?:\|[a-z0-9_]+(?:\.[a-z0-9_]+)*)*$")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _fail(msg):
    raise serializers.ValidationError(msg)


def _check_cell(cell, *, allow_question_id, where):
    if cell is None:
        return
    if not isinstance(cell, dict):
        _fail(f"{where}: cell must be an object or null")
    field = cell.get("field")
    if not isinstance(field, str) or not _FIELD_RE.match(field):
        _fail(f"{where}: cell.field {field!r} is not a valid field path")
    layout = cell.get("layout", "inline")
    if layout not in _LAYOUT_VALUES:
        _fail(f"{where}: cell.layout must be one of {sorted(_LAYOUT_VALUES)}")
    if "questionId" in cell:
        if not allow_question_id:
            _fail(f"{where}: cell.questionId only allowed on grid/list rows")
        if not isinstance(cell["questionId"], int) or cell["questionId"] <= 0:
            _fail(f"{where}: cell.questionId must be a positive int")
    if "filter" in cell:
        filt = cell["filter"]
        if not isinstance(filt, dict) or not filt or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in filt.items()
        ):
            _fail(f"{where}: cell.filter must be a non-empty object of string -> string")
    extra = set(cell) - {"field", "layout", "questionId", "filter"}
    if extra:
        _fail(f"{where}: unexpected cell keys {sorted(extra)}")


def _check_header(rows, where):
    # Empty means the source table had no real header row at all - a valid,
    # common shape (most `list`/`grid` tables converted from a plain
    # <tr><th>label</th><td>...</td></tr> layout have none).
    if not isinstance(rows, list):
        _fail(f"{where}: columnHeader must be a list of rows")
    for ri, row in enumerate(rows):
        if not isinstance(row, list) or not row:
            _fail(f"{where}: columnHeader[{ri}] must be a non-empty list")
        for ci, hc in enumerate(row):
            if not isinstance(hc, dict):
                _fail(f"{where}: columnHeader[{ri}][{ci}] must be an object")
            if not isinstance(hc.get("label", ""), str):
                _fail(f"{where}: columnHeader[{ri}][{ci}].label must be a string")
            for span in ("colspan", "rowspan"):
                if span in hc and (not isinstance(hc[span], int) or hc[span] < 1):
                    _fail(f"{where}: columnHeader[{ri}][{ci}].{span} must be a positive int")


def _check_table(t, where):
    if not isinstance(t, dict):
        _fail(f"{where}: table must be an object")
    kind = t.get("kind")
    if kind not in _KIND_VALUES:
        _fail(f"{where}: kind must be one of {sorted(_KIND_VALUES)}")
    if not isinstance(t.get("caption"), (str, type(None))):
        _fail(f"{where}: caption must be a string or null")
    rhw = t.get("rowHeaderWidth")
    if not isinstance(rhw, int) or rhw < 0:
        _fail(f"{where}: rowHeaderWidth must be an int >= 0")
    _check_header(t.get("columnHeader"), where)

    cols = t.get("columns")
    if not isinstance(cols, list):
        _fail(f"{where}: columns must be a list")
    for ci, col in enumerate(cols):
        if not isinstance(col, dict) or set(col) - {"cell"}:
            _fail(f"{where}: columns[{ci}] must be an object with only 'cell'")
        _check_cell(col.get("cell"), allow_question_id=False, where=f"{where}.columns[{ci}]")

    if kind == "template":
        if t.get("multiAnswer", "rows") not in _MULTI_VALUES:
            _fail(f"{where}: multiAnswer must be one of {sorted(_MULTI_VALUES)}")

    rows = t.get("rows")
    if not isinstance(rows, list):
        _fail(f"{where}: rows must be a list")
    for ri, row in enumerate(rows):
        rwhere = f"{where}.rows[{ri}]"
        if not isinstance(row, dict):
            _fail(f"{rwhere}: row must be an object")
        labels = row.get("labels")
        if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
            _fail(f"{rwhere}: labels must be a list of strings")
        if len(labels) > max(rhw, 1):
            _fail(f"{rwhere}: {len(labels)} labels exceed rowHeaderWidth {rhw}")
        if kind == "template":
            qid = row.get("questionId")
            if not isinstance(qid, int) or qid <= 0:
                _fail(f"{rwhere}: template row needs a positive questionId")
            if "cells" in row:
                _fail(f"{rwhere}: template row must not carry cells")
        else:
            cells = row.get("cells")
            if not isinstance(cells, list):
                _fail(f"{rwhere}: grid/list row needs a cells list")
            for ci, cell in enumerate(cells):
                _check_cell(cell, allow_question_id=True, where=f"{rwhere}.cells[{ci}]")
            if "questionId" in row:
                _fail(f"{rwhere}: grid/list row must not carry a row-level questionId")


def validate_spec(spec):
    """Raise ``rest_framework.serializers.ValidationError`` if *spec* is malformed."""
    if not isinstance(spec, dict):
        _fail("spec must be an object")
    if spec.get("schemaVersion") != SCHEMA_VERSION:
        _fail(f"schemaVersion must be {SCHEMA_VERSION}")
    if not isinstance(spec.get("title", ""), str) or not spec.get("title", "").strip():
        _fail("title must be a non-empty string")
    sections = spec.get("sections")
    if not isinstance(sections, list) or not sections:
        _fail("sections must be a non-empty list")
    for si, sec in enumerate(sections):
        if not isinstance(sec, dict):
            _fail(f"sections[{si}] must be an object")
        if not isinstance(sec.get("heading"), (str, type(None))):
            _fail(f"sections[{si}].heading must be a string or null")
        tables = sec.get("tables")
        if not isinstance(tables, list) or not tables:
            _fail(f"sections[{si}].tables must be a non-empty list")
        for ti, table in enumerate(tables):
            _check_table(table, f"sections[{si}].tables[{ti}]")


def validate_slug(slug):
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        _fail(f"slug {slug!r} must be lowercase words joined by single hyphens")


# ---------------------------------------------------------------------------
# legacy content -> spec
# ---------------------------------------------------------------------------

_TABLE_RE = re.compile(r"<table\b[^>]*>(.*?)</table>", re.IGNORECASE | re.DOTALL)
_CAPTION_RE = re.compile(r"<caption\b[^>]*>(.*?)</caption>", re.IGNORECASE | re.DOTALL)
_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

# top-level segmentation: <h1>, <h2>, <h3>, <table>...</table>
_SEGMENT_RE = re.compile(
    r"(?P<h1><h1\b[^>]*>.*?</h1>)"
    r"|(?P<h2><h2\b[^>]*>.*?</h2>)"
    r"|(?P<h3><h3\b[^>]*>.*?</h3>)"
    r"|(?P<table><table\b[^>]*>.*?</table>)",
    re.IGNORECASE | re.DOTALL,
)

_FOREACH_ROW_RE = re.compile(r"\[foreach\]\s*<tr", re.IGNORECASE)
_ENDFOREACH_ROW_RE = re.compile(r"</tr>\s*\[endforeach\]", re.IGNORECASE)
# One row template. Non-greedy up to the first `</tr> [endforeach]` - inner
# `[foreach]<div>…[endforeach]` cell wrappers carry no `</tr>` so they don't match.
_ROW_FOREACH_RE = re.compile(r"\[foreach\]\s*(<tr\b.*?</tr>)\s*\[endforeach\]", re.IGNORECASE | re.DOTALL)

_JAML_RE = re.compile(
    # tolerates ONE level of nested `{...}` inside the payload (e.g. a
    # `filter: {word_class: 'Adjective'}` clause) - a plain `[^{}]*` payload
    # regex can't skip over that nested brace pair and never matches at all,
    # which silently misclassifies the whole row as a header row (see
    # _header_rows) instead of a data row.
    r"\{(?:[^{}]|\{[^{}]*\})*\bid\b\s*:\s*\"?(\d+)\"?(?:[^{}]|\{[^{}]*\})*\}",
    re.IGNORECASE,
)
_JAML_FIELD_RE = re.compile(r"\bfield\s*:\s*\"?([^,}\"]+?)\"?\s*(?=[,}])", re.IGNORECASE)
_JAML_TABLEFIELD_RE = re.compile(r"\btableField\s*:\s*\"?([^,}\"]+?)\"?\s*(?=[,}])", re.IGNORECASE)
# `filter: {key: 'val', ...}` - selects which of several Answer docs sharing
# the same questionId this cell shows (e.g. distinguishing an Adjective stem
# from an Adverb stem recorded under the same research question).
_JAML_FILTER_RE = re.compile(r"\bfilter\s*:\s*\{([^{}]*)\}", re.IGNORECASE)
_FILTER_PAIR_RE = re.compile(r"[\"']?(\w+)[\"']?\s*:\s*\"?'?([^,\"'}]+?)'?\"?\s*(?=,|\Z)")
_FOREACH_DIV_RE = re.compile(r"\[foreach\]\s*<div>", re.IGNORECASE)


def _text(fragment):
    return html.unescape(re.sub(r"\s+", " ", _TAG_RE.sub("", fragment or "")).strip())


class _CellParser(HTMLParser):
    """Collects the <th>/<td> cells of one <tr> fragment with their raw inner text/markup."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells = []  # list of {tag, attrs, inner}
        self._stack = []

    def handle_starttag(self, tag, attrs):
        if tag in ("th", "td"):
            self._stack.append({"tag": tag, "attrs": dict(attrs), "parts": []})
        elif self._stack:
            a = " ".join(f'{k}="{v}"' if v is not None else k for k, v in attrs)
            self._stack[-1]["parts"].append(f"<{tag}{(' ' + a) if a else ''}>")

    def handle_endtag(self, tag):
        if tag in ("th", "td") and self._stack:
            cell = self._stack.pop()
            cell["inner"] = "".join(cell["parts"])
            self.cells.append(cell)
        elif self._stack:
            self._stack[-1]["parts"].append(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        if self._stack:
            self._stack[-1]["parts"].append(f"<{tag}/>")

    def handle_data(self, data):
        if self._stack:
            self._stack[-1]["parts"].append(data)


def _parse_row(tr_fragment):
    p = _CellParser()
    p.feed(tr_fragment)
    p.close()
    return p.cells


def _int_attr(attrs, name, default=1):
    try:
        return int(attrs.get(name, default))
    except (TypeError, ValueError):
        return default


def _cell_binding(inner):
    """Legacy cell inner text -> CellBinding dict (with ``questionId``), or None if it holds no JAML."""
    if not _JAML_RE.search(inner):
        return None
    layout = "stack" if _FOREACH_DIV_RE.search(inner) else "inline"
    id_m = _JAML_RE.search(inner)
    field_m = _JAML_FIELD_RE.search(inner)
    tf_m = _JAML_TABLEFIELD_RE.search(inner)
    field = (field_m.group(1).strip() if field_m else "").replace(":", "|")
    if tf_m:
        tf = tf_m.group(1).strip()
        # old: field=<container>, tableField=<projection(s)>  ->  new: <container>.<projection>
        parts = [f"{field}.{seg.strip()}" for seg in tf.split("|")]
        field = "|".join(parts)
    binding = {"field": field, "layout": layout}
    if id_m:
        binding["questionId"] = int(id_m.group(1))
    filter_m = _JAML_FILTER_RE.search(inner)
    if filter_m:
        pairs = _FILTER_PAIR_RE.findall(filter_m.group(1))
        if pairs:
            binding["filter"] = dict(pairs)
    return binding


def _split_sections(content):
    """-> (title, [ {heading, raw_tables:[str]} ])."""
    title = ""
    sections = [{"heading": None, "raw_tables": []}]
    for m in _SEGMENT_RE.finditer(content):
        kind = m.lastgroup
        chunk = m.group(kind)
        if kind == "h1":
            title = _text(chunk)
        elif kind in ("h2", "h3"):
            heading = _text(chunk)
            if sections[-1]["raw_tables"] or sections[-1]["heading"] is not None:
                sections.append({"heading": heading, "raw_tables": []})
            else:
                sections[-1]["heading"] = heading
        else:  # table
            sections[-1]["raw_tables"].append(chunk)
    sections = [s for s in sections if s["raw_tables"]]
    return title, sections


def _header_rows(rows):
    """Leading <tr>s that carry no JAML are the column header."""
    header = []
    for r in rows:
        if _JAML_RE.search(r):
            break
        header.append(r)
    return header, rows[len(header) :]


def _template_rows(row_trs):
    """Template ([foreach]) rows: labels repeat every row, no native rowspan carry."""
    parsed = []
    for tr in row_trs:
        labels, bindings, qid = [], [], None
        seen_data = False
        for c in _parse_row(tr):
            b = _cell_binding(c["inner"])
            if c["tag"] == "th" and b is None and not seen_data:
                labels.append(_text(c["inner"]))
                continue
            seen_data = True
            bindings.append(b)
            if b is not None and qid is None:
                qid = b.get("questionId")
        parsed.append({"labels": labels, "bindings": bindings, "qid": qid})
    return parsed


def _grid_rows(row_trs):
    """Static grid rows: expand colspan and carry native rowspan down, so a
    ``<th rowspan=4>`` label re-appears in the covered rows at its column - the
    same reconstruction the legacy client does with its ``activeRowspans`` map."""
    active = {}  # col -> {"item": ("label"|"data", value), "remaining": int}
    parsed = []
    for tr in row_trs:
        cells = _parse_row(tr)
        items, col, ci = [], 0, 0
        while ci < len(cells) or any(k >= col for k in active):
            if col in active:
                a = active[col]
                items.append(a["item"])
                a["remaining"] -= 1
                if a["remaining"] <= 0:
                    del active[col]
                col += 1
                continue
            if ci >= len(cells):
                col += 1
                continue
            c = cells[ci]
            ci += 1
            b = _cell_binding(c["inner"])
            is_label = c["tag"] == "th" and b is None
            item = ("label", _text(c["inner"])) if is_label else ("data", b)
            for _ in range(_int_attr(c["attrs"], "colspan")):
                items.append(item)
                if _int_attr(c["attrs"], "rowspan") > 1:
                    active[col] = {"item": item, "remaining": _int_attr(c["attrs"], "rowspan") - 1}
                col += 1
        labels, bindings, seen_data = [], [], False
        for kind, val in items:
            if kind == "label" and not seen_data:
                labels.append(val)
            elif kind == "data":
                seen_data = True
                bindings.append(val)
        qid = next((b.get("questionId") for b in bindings if b), None)
        parsed.append({"labels": labels, "bindings": bindings, "qid": qid})
    return parsed


def _parse_table(raw_table):
    body = raw_table
    caption_m = _CAPTION_RE.search(body)
    caption = _text(caption_m.group(1)) if caption_m else None
    body = _CAPTION_RE.sub("", body)

    is_template = bool(_FOREACH_ROW_RE.search(body) and _ENDFOREACH_ROW_RE.search(body))

    if is_template:
        # header is whatever <tr> sits before the first [foreach]
        pre = body[: body.lower().index("[foreach]")]
        header_trs = [f"<tr>{x}</tr>" for x in _TR_RE.findall(pre)]
        row_trs = _ROW_FOREACH_RE.findall(body)
    else:
        all_trs = [f"<tr>{x}</tr>" for x in _TR_RE.findall(body)]
        header_list, data_list = _header_rows(all_trs)
        header_trs = header_list
        row_trs = data_list

    # --- column header matrix ------------------------------------------------
    column_header = []
    for htr in header_trs:
        hrow = []
        for c in _parse_row(htr):
            hc = {"label": _text(c["inner"])}
            cs = _int_attr(c["attrs"], "colspan")
            rs = _int_attr(c["attrs"], "rowspan")
            if cs > 1:
                hc["colspan"] = cs
            if rs > 1:
                hc["rowspan"] = rs
            hrow.append(hc)
        if hrow:
            column_header.append(hrow)

    # --- rows --------------------------------------------------------------
    parsed_rows = _template_rows(row_trs) if is_template else _grid_rows(row_trs)
    max_label_width = max((len(r["labels"]) for r in parsed_rows), default=0)
    data_col_counts = [len(r["bindings"]) for r in parsed_rows]

    # rowHeaderWidth: header corner colspan vs deepest body label nesting
    corner_span = 0
    if column_header:
        first = column_header[0][0] if column_header[0] else {}
        if not first.get("label"):
            corner_span = first.get("colspan", 1)
    row_header_width = max(corner_span, max_label_width)

    n_data_cols = max(data_col_counts) if data_col_counts else max(
        len(column_header[0]) - row_header_width if column_header else 0, 0
    )

    if is_template:
        kind = "template"
        columns = [{"cell": None} for _ in range(n_data_cols)]
        # template columns take their field from the first row's bindings; the
        # question id lives on the row, not the column.
        if parsed_rows:
            for i, b in enumerate(parsed_rows[0]["bindings"]):
                if i < len(columns) and b is not None:
                    columns[i]["cell"] = {k: v for k, v in b.items() if k != "questionId"}
        rows = [{"labels": r["labels"], "questionId": r["qid"]} for r in parsed_rows]
    else:
        kind = "list" if n_data_cols <= 1 else "grid"
        columns = [{"cell": None} for _ in range(n_data_cols)]
        rows = []
        for r in parsed_rows:
            cells = list(r["bindings"])  # each keeps its own questionId
            cells += [None] * (n_data_cols - len(cells))
            rows.append({"labels": r["labels"], "cells": cells})

    table = {
        "kind": kind,
        "caption": caption,
        "rowHeaderWidth": row_header_width,
        # Empty (not a synthetic single blank cell) when the source table had
        # no real header <tr> at all - a fabricated single-column blank
        # header used to render as a stray partial-width bar next to the
        # data columns it didn't cover, since it only ever spanned 1 of N
        # physical columns.
        "columnHeader": column_header,
        "columns": columns,
        "rows": rows,
    }
    if kind == "template":
        table["multiAnswer"] = "rows"
    return table


def parse_view_content(content):
    """Legacy ``Views.content`` HTML string -> ``spec`` dict (schema v1)."""
    content = content or ""
    title, sections = _split_sections(content)
    if not title:
        title = _text(_H1_RE.search(content).group(1)) if _H1_RE.search(content) else "Untitled"
    out_sections = []
    for sec in sections:
        out_sections.append(
            {
                "heading": sec["heading"],
                "tables": [_parse_table(rt) for rt in sec["raw_tables"]],
            }
        )
    if not out_sections:
        out_sections = [{"heading": None, "tables": []}]
    return {"schemaVersion": SCHEMA_VERSION, "title": title, "sections": out_sections}


# ---------------------------------------------------------------------------
# parity signatures
# ---------------------------------------------------------------------------


def html_signature(content):
    """Reduced structural fingerprint derived straight from the legacy HTML."""
    title, sections = _split_sections(content or "")
    sig_sections = []
    for sec in sections:
        tabs = []
        for rt in sec["raw_tables"]:
            body = _CAPTION_RE.sub("", rt)
            is_tmpl = bool(_FOREACH_ROW_RE.search(body) and _ENDFOREACH_ROW_RE.search(body))
            if is_tmpl:
                parsed = _template_rows(_ROW_FOREACH_RE.findall(body))
            else:
                all_trs = [f"<tr>{x}</tr>" for x in _TR_RE.findall(body)]
                _, data_trs = _header_rows(all_trs)
                parsed = _grid_rows(data_trs)
            row_sigs = []
            for r in parsed:
                ids = tuple(b.get("questionId") for b in r["bindings"] if b is not None)
                fields = tuple(
                    (b["field"] + ("!" if b["layout"] == "stack" else "")) if b is not None else None
                    for b in r["bindings"]
                )
                row_sigs.append((tuple(r["labels"]), ids, fields))
            tabs.append((is_tmpl, tuple(row_sigs)))
        sig_sections.append((sec["heading"], tuple(tabs)))
    return (title, tuple(sig_sections))


def spec_signature(spec):
    """The same fingerprint, derived from a produced spec - must equal html_signature."""
    sig_sections = []
    for sec in spec["sections"]:
        tabs = []
        for t in sec["tables"]:
            is_tmpl = t["kind"] == "template"
            row_sigs = []
            for r in t["rows"]:
                labels = tuple(r["labels"])
                if is_tmpl:
                    ids = tuple(
                        r["questionId"] for c in t["columns"] if c["cell"] is not None
                    )
                    fields = tuple(
                        (c["cell"]["field"] + ("!" if c["cell"].get("layout") == "stack" else ""))
                        if c["cell"] is not None
                        else None
                        for c in t["columns"]
                    )
                else:
                    ids = tuple(c["questionId"] for c in r["cells"] if c is not None)
                    fields = tuple(
                        (c["field"] + ("!" if c.get("layout") == "stack" else ""))
                        if c is not None
                        else None
                        for c in r["cells"]
                    )
                row_sigs.append((labels, ids, fields))
            tabs.append((is_tmpl, tuple(row_sigs)))
        sig_sections.append((sec["heading"], tuple(tabs)))
    return (spec["title"], tuple(sig_sections))
