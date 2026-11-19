#!/usr/bin/env python3
"""
Download every RLD audio file, using the database's own metadata to know what
to fetch. Standalone: Python 3.9+ standard library only, no Django, no
credentials for the public site.

Audio lives under <base-url>/<sample>/ with three kinds of file (the same
names the web client builds, see roma-client phrases/transcriptions
components):

    <sample>_<phrase_ref>.mp3    one per SamplePhrases record with has_recording
    <sample>_SEG_<segment>.mp3   one per Transcriptions record (connected speech)
    <sample>_TRANS.mp3           the whole recording, per sample with transcriptions

Metadata comes from either
  --dump DIR     an unpacked arangodump directory (no database needed), or
  --arango URL   a running ArangoDB (e.g. a restored copy), via its HTTP API.

Re-running is safe: files already on disk with the server's size are skipped,
so the script also works as an incremental mirror. --listing additionally
walks the server's directory listing and fetches files the metadata doesn't
mention (and reports them), as a completeness check.

A manifest (CSV: kind, sample, ref, path, status, bytes, sha256) is written to
<out>/manifest.csv. Exit code is 1 if any download failed (404s for expected
files are reported, not fatal).

Examples:
    python3 download_audio.py --dump ./dump-multiple --out ./rld-audio
    python3 download_audio.py --arango http://localhost:8529 --db rms \\
        --user root --password-env ARANGO_PASSWORD --out ./rld-audio --listing
    python3 download_audio.py --dump ./dump-multiple --out ./a --samples RO-022,AL-001
"""

import argparse
import base64
import csv
import glob
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_BASE_URL = "https://romanidb.eu/mp3"
USER_AGENT = "rld-audio-downloader/1.0"


# --- metadata sources --------------------------------------------------------

def dump_records(dump_dir, collection):
    """Yield the documents of `collection` from an arangodump directory."""
    paths = glob.glob(os.path.join(dump_dir, f"{collection}_*.data.json*")) or glob.glob(
        os.path.join(dump_dir, f"{collection}.data.json*")
    )
    if not paths:
        sys.exit(f"No dump file for collection {collection!r} in {dump_dir}")
    for path in paths:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                doc = json.loads(line)
                # Dumps made with --envelope wrap each document as {"type":..., "data":{...}}.
                if "data" in doc and "type" in doc:
                    if doc["type"] != 2300:  # 2300 = document insert/replace
                        continue
                    doc = doc["data"]
                yield doc


def _arango_query(url, db, user, password, aql):
    """Run an AQL query over the HTTP cursor API and yield all results."""
    auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def call(method, path, body=None):
        req = urllib.request.Request(
            f"{url.rstrip('/')}/_db/{urllib.parse.quote(db)}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Authorization": auth, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.load(resp)

    res = call("POST", "/_api/cursor", {"query": aql, "batchSize": 10000})
    yield from res["result"]
    while res.get("hasMore"):
        res = call("POST", f"/_api/cursor/{res['id']}")
        yield from res["result"]


def load_metadata(args):
    """Return (phrases, segments): lists of (sample, ref) tuples."""
    if args.dump:
        phrases = (
            (d.get("sample"), d.get("phrase_ref"), d.get("has_recording"))
            for d in dump_records(args.dump, "SamplePhrases")
        )
        segments = ((d.get("sample"), d.get("segment_no")) for d in dump_records(args.dump, "Transcriptions"))
    else:
        password = os.environ.get(args.password_env, "") if args.password_env else (args.password or "")
        q = lambda aql: _arango_query(args.arango, args.db, args.user, password, aql)  # noqa: E731
        phrases = (
            (d["sample"], d["phrase_ref"], d["has_recording"])
            for d in q("FOR p IN SamplePhrases RETURN {sample: p.sample, phrase_ref: p.phrase_ref, has_recording: p.has_recording}")
        )
        segments = ((d["sample"], d["segment_no"]) for d in q("FOR t IN Transcriptions RETURN {sample: t.sample, segment_no: t.segment_no}"))

    wanted = set(args.samples.split(",")) if args.samples else None
    keep = lambda s: s and (wanted is None or s in wanted)  # noqa: E731
    # has_recording is a bool on current data but a "True"/"False" string on older dumps.
    phrase_list = sorted(
        {(s, str(r)) for s, r, rec in phrases if keep(s) and r is not None and (args.all_phrases or str(rec) == "True")}
    )
    segment_list = sorted({(s, str(n)) for s, n in segments if keep(s) and n is not None})
    return phrase_list, segment_list


def build_targets(phrases, segments):
    """List of (kind, sample, ref, relative path) for every expected file."""
    targets = [("phrase", s, r, f"{s}/{s}_{r}.mp3") for s, r in phrases]
    targets += [("segment", s, n, f"{s}/{s}_SEG_{n}.mp3") for s, n in segments]
    targets += [("transcription", s, "TRANS", f"{s}/{s}_TRANS.mp3") for s in sorted({s for s, _ in segments})]
    return targets


def listing_targets(base_url, samples=None):
    """Walk the server's nginx autoindex and return every .mp3 path under it."""
    href = re.compile(r'href="([^"?]+)"')

    def ls(path):
        with urllib.request.urlopen(_request(f"{base_url}/{path}"), timeout=60) as resp:
            return [h for h in href.findall(resp.read().decode("utf-8", "replace")) if not h.startswith("..")]

    found = []
    for d in ls(""):
        if not d.endswith("/") or (samples and d.rstrip("/") not in samples):
            continue
        found += [f"{urllib.parse.unquote(d)}{urllib.parse.unquote(f)}" for f in ls(d) if f.lower().endswith(".mp3")]
    return found


# --- downloading -------------------------------------------------------------

def _request(url, method="GET"):
    return urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(base_url, out_dir, rel_path, retries=3):
    """Download one file. Returns (status, bytes): ok / skipped / missing / failed."""
    url = f"{base_url}/{urllib.parse.quote(rel_path)}"
    dest = os.path.join(out_dir, rel_path)
    for attempt in range(1, retries + 1):
        try:
            if os.path.exists(dest):
                with urllib.request.urlopen(_request(url, "HEAD"), timeout=60) as resp:
                    size = resp.headers.get("Content-Length")
                if size is not None and int(size) == os.path.getsize(dest):
                    return "skipped", os.path.getsize(dest)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + ".part"
            with urllib.request.urlopen(_request(url), timeout=120) as resp, open(tmp, "wb") as fh:
                while chunk := resp.read(1 << 16):
                    fh.write(chunk)
            os.replace(tmp, dest)
            return "ok", os.path.getsize(dest)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "missing", 0
            err = e
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            err = e
        time.sleep(2 ** attempt)
    print(f"  FAILED {rel_path}: {err}", file=sys.stderr)
    return "failed", 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--dump", help="unpacked arangodump directory (e.g. dump-multiple/)")
    src.add_argument("--arango", help="ArangoDB URL, e.g. http://localhost:8529")
    ap.add_argument("--db", default="rms", help="Arango database name (default: rms)")
    ap.add_argument("--user", default="root", help="Arango user (default: root)")
    ap.add_argument("--password", help="Arango password (prefer --password-env)")
    ap.add_argument("--password-env", help="name of an environment variable holding the Arango password")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"audio base URL (default: {DEFAULT_BASE_URL})")
    ap.add_argument("--samples", help="comma-separated sample refs to limit to (default: all)")
    ap.add_argument("--all-phrases", action="store_true", help="also try phrases not flagged has_recording")
    ap.add_argument("--listing", action="store_true", help="also fetch files found only in the server's directory listing")
    ap.add_argument("--workers", type=int, default=8, help="parallel downloads (default: 8)")
    args = ap.parse_args()
    base_url = args.base_url.rstrip("/")

    print("Reading metadata ...")
    phrases, segments = load_metadata(args)
    targets = build_targets(phrases, segments)
    print(f"  {len(phrases)} phrase recordings, {len(segments)} transcription segments, "
          f"{len({s for s, _ in segments})} full transcriptions")

    if args.listing:
        print("Reading server directory listing ...")
        known = {t[3] for t in targets}
        extra = [p for p in listing_targets(base_url, set(args.samples.split(",")) if args.samples else None) if p not in known]
        print(f"  {len(extra)} files on the server not referenced in the metadata")
        targets += [("unreferenced", p.split("/")[0], "", p) for p in extra]

    os.makedirs(args.out, exist_ok=True)
    results = []
    counts = {"ok": 0, "skipped": 0, "missing": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch, base_url, args.out, t[3]): t for t in targets}
        for i, fut in enumerate(as_completed(futures), 1):
            status, size = fut.result()
            counts[status] += 1
            results.append((*futures[fut], status, size))
            if i % 500 == 0 or i == len(targets):
                print(f"  {i}/{len(targets)}  " + "  ".join(f"{k}={v}" for k, v in counts.items()))

    manifest = os.path.join(args.out, "manifest.csv")
    with open(manifest, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["kind", "sample", "ref", "path", "status", "bytes", "sha256"])
        for kind, sample, ref, path, status, size in sorted(results, key=lambda r: r[3]):
            local = os.path.join(args.out, path)
            digest = _sha256(local) if status in ("ok", "skipped") else ""
            w.writerow([kind, sample, ref, path, status, size, digest])

    print(f"Done: {counts['ok']} downloaded, {counts['skipped']} already present, "
          f"{counts['missing']} not on server, {counts['failed']} failed. Manifest: {manifest}")
    sys.exit(1 if counts["failed"] else 0)


if __name__ == "__main__":
    main()
