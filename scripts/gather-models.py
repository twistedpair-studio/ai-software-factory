#!/usr/bin/env python3
"""gather-models.py - deterministic data gathering for the model-scout agent.

Collects model data from three places and writes a dated snapshot:

  1. OpenCode Go docs page   -> prices, quotas, request estimates, privacy, model IDs
  2. OpenCode Go /v1/models  -> which model IDs the API actually serves right now
  3. `models` CLI (reyamira/models, `cargo install modelsdev` or `brew install models`)
                             -> models.dev catalog (capabilities, context, cost) + benchmarks

Everything is plain data. No LLM is involved, so the numbers the scout reasons
about are never written from memory.

Usage (run from the project root):
  python3 .agents/scripts/gather-models.py                      # gather a snapshot (Go plan tables)
  python3 .agents/scripts/gather-models.py --plan plus          # use the Go Plus tables if the docs page has them
  python3 .agents/scripts/gather-models.py --quota-multiplier 4 # manual override if Plus tables can't be parsed
  python3 .agents/scripts/gather-models.py validate FILE        # check model IDs in FILE exist

Output (under .agents/snapshots/):
  YYYY-MM-DD/summary.json   unified view: one entry per Go model + diff vs previous run
  YYYY-MM-DD/raw-*.json     raw tool output, for debugging
  latest.json               copy of the newest summary.json  (what the scout reads)
  latest-diff.json          copy of the diff section         (what changed since last run)

Python 3.9+, standard library only. Exit codes: 0 ok, 1 validation failure, 2 critical failure.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

GO_DOCS_URL = "https://opencode.ai/docs/go/"
# The docs have used both paths over time; try each until one answers.
GO_MODELS_URLS = [
    "https://opencode.ai/inference/go/v1/models",
    "https://opencode.ai/zen/go/v1/models",
]
USER_AGENT = "model-scout-gather/1.0"

# Matches provider-qualified model references such as opencode-go/glm-5.3
REF_RE = re.compile(r"\b(opencode-go|opencode)/([A-Za-z0-9][A-Za-z0-9._\-]*)")

# Suffixes benchmark sites append to model names (effort / reasoning variants).
# Deliberately excludes "max", "flash", "pro", "preview" - those are real model names.
VARIANT_TOKENS = {"reasoning", "thinking", "adaptive", "effort", "high", "medium", "low", "xhigh", "minimal"}
BENCH_NAME_KEYS = ("name", "model", "model_name", "display_name", "slug", "id")
DIFF_FIELDS = ("pricing", "monthly_limit_usd", "requests", "privacy", "promo", "free", "endpoint", "in_endpoint")


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# --------------------------------------------------------------------------- #
# HTTP + docs-page table parsing
# --------------------------------------------------------------------------- #
def http_get(url: str, headers: dict | None = None, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class TableParser(HTMLParser):
    """Collects every <table> as a list of rows of plain-text cells."""

    INLINE = ("del", "s", "strong", "b", "ins", "em", "span", "code")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif self._cell is not None and (tag == "br" or tag in self.INLINE):
            self._cell.append(" ")  # keeps struck-through and promo values apart

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row and self._table is not None:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None
        elif self._cell is not None and tag in self.INLINE:
            self._cell.append(" ")

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def classify(header: list[str]) -> str:
    h = [c.lower() for c in header]
    if h and h[0] == "model":
        if "model id" in h:
            return "endpoints"
        if "model training" in h:
            return "privacy"
        if "cached read" in h:
            return "pricing"
        if any("requests per" in c for c in h):
            return "requests"
    if h and h[0] == "plan":
        return "plans"
    return "other"


def rows_as_dicts(table: list[list[str]]) -> list[dict[str, str]]:
    header = [c.lower() for c in table[0]]
    return [dict(zip(header, row)) for row in table[1:]]


def num(text: str | None, dollar: bool = False) -> float | None:
    """Last number in a cell. Promo cells look like '220 440' (old, new), so last wins."""
    if not text:
        return None
    pat = r"\$\s*([\d,]+(?:\.\d+)?)" if dollar else r"([\d,]+(?:\.\d+)?)"
    found = re.findall(pat, text)
    if not found:
        return None
    value = float(found[-1].replace(",", ""))
    return int(value) if value.is_integer() and not dollar else value


def split_name(name: str) -> tuple[str, str | None]:
    """'GPT 6 Luna (<= 272K tokens)' -> ('GPT 6 Luna', '<= 272K tokens')."""
    m = re.match(r"^(.*?)\s*\((.+)\)\s*$", name.strip())
    return (m.group(1), m.group(2)) if m else (name.strip(), None)


PLAN_TABLE_KINDS = ('pricing', 'requests')  # the docs show Go / Go Plus tabs for these two tables only


def build_go_models(tables: list[list[list[str]]], plan_index: int = 0) -> tuple[dict, list[str], list[str]]:
    """Turn the docs-page tables into {model_id: {...}}. Returns (models, warnings, notes)."""
    by_kind: dict[str, list] = {}
    for t in tables:
        if t:
            by_kind.setdefault(classify(t[0]), []).append(t)

    warnings: list[str] = []
    notes: list[str] = []

    def first(kind: str) -> list[dict[str, str]]:
        found = by_kind.get(kind)
        if not found:
            warnings.append(f"docs table '{kind}' not found - page layout may have changed")
            return []
        idx = plan_index if kind in PLAN_TABLE_KINDS else 0
        if kind in PLAN_TABLE_KINDS:
            if idx >= len(found):
                warnings.append(
                    f"docs table '{kind}': only {len(found)} table(s) found, so Go Plus values were not located; "
                    "showing the first (Go plan) table - quotas are likely understated. Check your console usage page "
                    "and use --quota-multiplier to correct"
                )
                idx = 0
            elif len(found) > 1:
                notes.append(f"docs table '{kind}' appears {len(found)}x; using table #{idx + 1} as the {'Go Plus' if idx else 'Go'} plan (assumes tab order Go, Go Plus)")
        return rows_as_dicts(found[idx])

    models: dict[str, dict] = {}
    known: list[str] = []

    def resolve(raw: str) -> str:
        base = split_name(raw)[0]
        best = ""
        for n in known:
            rest = base[len(n):]
            if base.lower().startswith(n.lower()) and (rest == "" or rest[0] == " ") and len(n) > len(best):
                best = n
        return best or base

    def get(raw: str) -> dict:
        name = resolve(raw)
        return models.setdefault(name, {"name": name})

    for r in first("endpoints"):
        name = split_name(r.get("model", ""))[0]
        if not name:
            continue
        known.append(name)
        models.setdefault(name, {"name": name}).update(
            id=r.get("model id") or None, endpoint=r.get("endpoint"), sdk=r.get("ai sdk package")
        )

    for r in first("pricing"):
        raw = r.get("model", "")
        m = get(raw)
        tier = split_name(raw)[1] or "default"
        limit = r.get("monthly limit", "")
        m.setdefault("pricing", {})[tier] = {
            "input": num(r.get("input"), True),
            "output": num(r.get("output"), True),
            "cached_read": num(r.get("cached read"), True),
            "cached_write": num(r.get("cached write"), True),
        }
        m["monthly_limit_usd"] = num(limit, True)
        m["unlimited"] = "unlimited" in limit.lower()
        m["free"] = r.get("input", "").strip().lower() == "free"
        if "limited time" in limit.lower():
            m["promo"] = limit

    for r in first("requests"):
        raw = r.get("model", "")
        m = get(raw)
        m["requests"] = {
            "per_5h": num(r.get("requests per 5 hours")),
            "per_week": num(r.get("requests per week")),
            "per_month": num(r.get("requests per month")),
        }
        if "limited time" in raw.lower():
            m["promo_requests"] = raw

    for r in first("privacy"):
        m = get(r.get("model", ""))
        training = r.get("model training", "")
        m["privacy"] = {
            "training": training,
            "retention": r.get("data retention", ""),
            "trains_on_prompts": training.strip().lower().startswith("yes"),
        }

    out: dict[str, dict] = {}
    for name, m in models.items():
        if m.get("id"):
            m["id_source"] = "docs"
        else:
            m["id"] = re.sub(r"[^a-z0-9.]+", "-", name.lower()).strip("-")
            m["id_source"] = "derived (not in endpoints table)"
        out[m["id"]] = m
    return out, warnings, notes


# --------------------------------------------------------------------------- #
# `models` CLI + benchmark matching
# --------------------------------------------------------------------------- #
def run_models(args: list[str], timeout: int):
    exe = shutil.which("models")
    if not exe:
        raise RuntimeError("`models` CLI not found on PATH (brew install models | cargo install modelsdev)")
    p = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise RuntimeError(f"`models {' '.join(args)}` exited {p.returncode}: {p.stderr.strip()[:300]}")
    out = p.stdout.strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        starts = [i for i in (out.find("["), out.find("{")) if i >= 0]
        if not starts:
            raise RuntimeError(f"`models {' '.join(args)}` produced no JSON")
        return json.loads(out[min(starts):])


def extract_list(obj) -> list:
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        lists = [v for v in obj.values() if isinstance(v, list)]
        if lists:
            return max(lists, key=len)
    return []


def collect_ids(obj, keys=("id", "model_id", "slug")) -> set[str]:
    found: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in keys and isinstance(v, str):
                    found.add(v)
                walk(v)
        elif isinstance(o, list):
            for i in o:
                walk(i)

    walk(obj)
    return found


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def key_exact(s: str) -> tuple:
    return tuple(sorted(_tokens(s)))


def key_base(s: str) -> tuple:
    return tuple(sorted(t for t in _tokens(s) if t not in VARIANT_TOKENS))


def bench_matches(names: list[str], entries: list, cap: int = 6) -> list[dict]:
    """Match benchmark entries to a model by token-set equality. 'variant' = matched
    after stripping effort/reasoning suffixes, so the scout should double-check it."""
    exact = {key_exact(n) for n in names if _tokens(n)}
    base = {key_base(n) for n in names if _tokens(n)}
    found = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        cands = [e[k] for k in BENCH_NAME_KEYS if isinstance(e.get(k), str)]
        if any(key_exact(c) in exact for c in cands):
            found.append({"match": "exact", "entry": e})
        elif any(key_base(c) in base for c in cands):
            found.append({"match": "variant", "entry": e})
    found.sort(key=lambda x: x["match"] != "exact")
    return found[:cap]


# --------------------------------------------------------------------------- #
# Snapshot helpers
# --------------------------------------------------------------------------- #
def extract_refs(text: str) -> list[str]:
    seen, refs = set(), []
    for provider, mid in REF_RE.findall(text):
        ref = f"{provider}/{mid.rstrip('.-')}"
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


def diff_summaries(prev: dict, cur: dict) -> dict:
    pm, cm = prev.get("go_models", {}), cur.get("go_models", {})
    changed = {}
    for mid in set(pm) & set(cm):
        ch = {f: {"from": pm[mid].get(f), "to": cm[mid].get(f)} for f in DIFF_FIELDS if pm[mid].get(f) != cm[mid].get(f)}
        if ch:
            changed[mid] = ch
    pz, cz = prev.get("zen_models", {}), cur.get("zen_models", {})
    zen_changed = {}
    for zid in set(pz) & set(cz):
        if cz[zid].get("watched") and pz[zid].get("models_dev") != cz[zid].get("models_dev"):
            zen_changed[zid] = {"from": pz[zid].get("models_dev"), "to": cz[zid].get("models_dev")}
    return {
        "previous_snapshot": prev.get("generated_at"),
        "added_models": sorted(set(cm) - set(pm)),
        "removed_models": sorted(set(pm) - set(cm)),
        "changed": changed,
        "zen_added": sorted(set(cz) - set(pz)) if pz else [],
        "zen_removed": sorted(set(pz) - set(cz)),
        "zen_changed": zen_changed,
    }


def previous_snapshot(snap_root: Path, today: str) -> dict | None:
    dirs = sorted(d for d in snap_root.iterdir() if d.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name) and d.name < today)
    for d in reversed(dirs):
        f = d / "summary.json"
        if f.exists():
            return json.loads(f.read_text())
    return None


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_gather(args) -> int:
    agents = Path(args.agents_dir)
    snap_root = agents / "snapshots"
    today = dt.date.today().isoformat()
    out_dir = snap_root / today
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    sources: dict[str, dict] = {}

    def attempt(label, fn):
        try:
            return fn()
        except Exception as exc:  # keep going: partial data still beats none
            warnings.append(f"{label}: {exc}")
            log(f"WARN {label}: {exc}")
            return None

    # 1. Go docs page -----------------------------------------------------
    go_models, notes = {}, []
    html = attempt("go docs fetch", lambda: http_get(GO_DOCS_URL, timeout=args.timeout))
    if html:
        (out_dir / "raw-go-docs.html").write_text(html)
        parser = TableParser()
        parser.feed(html)
        write_json(out_dir / "raw-go-docs-tables.json", parser.tables)
        go_models, w, notes = build_go_models(parser.tables, 1 if args.plan == "plus" else 0)
        warnings += w
        if args.quota_multiplier != 1.0:
            for m in go_models.values():
                if m.get("monthly_limit_usd"):
                    m["monthly_limit_usd"] = round(m["monthly_limit_usd"] * args.quota_multiplier, 2)
                for k, v in (m.get("requests") or {}).items():
                    if v:
                        m["requests"][k] = int(v * args.quota_multiplier)
            notes.append(f"quota figures multiplied by {args.quota_multiplier} (manual --quota-multiplier)")
        sources["go_docs"] = {"url": GO_DOCS_URL, "sha256": hashlib.sha256(html.encode()).hexdigest(), "tables": len(parser.tables), "plan": args.plan, "quota_multiplier": args.quota_multiplier}

    # 2. Go models endpoint ----------------------------------------------
    headers = {"Authorization": f"Bearer {os.environ['OPENCODE_API_KEY']}"} if os.environ.get("OPENCODE_API_KEY") else {}
    endpoint_ids: set[str] = set()
    for url in GO_MODELS_URLS:
        body = attempt(f"go endpoint {url}", lambda u=url: http_get(u, headers, args.timeout))
        if body:
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                warnings.append(f"go endpoint {url}: response was not JSON")
                continue
            write_json(out_dir / "raw-go-endpoint.json", data)
            endpoint_ids = collect_ids(data)
            sources["go_endpoint"] = {"url": url, "ids": len(endpoint_ids)}
            break
    if not endpoint_ids:
        warnings.append("go endpoint: no model IDs retrieved (may need OPENCODE_API_KEY); availability not cross-checked")

    # 3. models CLI ------------------------------------------------------
    version = attempt("models --version", lambda: subprocess.run(["models", "--version"], capture_output=True, text=True, timeout=30).stdout.strip())
    providers = attempt("models providers", lambda: run_models(["providers", "--json"], args.timeout))
    if providers is not None:
        write_json(out_dir / "raw-models-providers.json", providers)
    go_rows = attempt("models list opencode-go", lambda: run_models(["list", "opencode-go", "--json"], args.timeout))
    zen_rows = attempt("models list opencode", lambda: run_models(["list", "opencode", "--json"], args.timeout))
    bench = attempt("models benchmarks list", lambda: run_models(["benchmarks", "list", "--json"], args.timeout))
    for label, obj in (("go-list", go_rows), ("zen-list", zen_rows), ("benchmarks", bench)):
        if obj is not None:
            write_json(out_dir / f"raw-models-{label}.json", obj)

    def by_id(obj) -> dict:
        return {r["id"]: r for r in extract_list(obj) if isinstance(r, dict) and isinstance(r.get("id"), str)}

    go_by_id, zen_by_id = by_id(go_rows), by_id(zen_rows)
    bench_entries = extract_list(bench)
    sources["models_cli"] = {"version": version, "benchmark_entries": len(bench_entries), "go_rows": len(go_by_id), "zen_rows": len(zen_by_id)}

    # 4. Enrich + cross-check -------------------------------------------
    for mid, m in go_models.items():
        m["in_endpoint"] = (mid in endpoint_ids) if endpoint_ids else None
        m["models_dev"] = go_by_id.get(mid)
        m["benchmarks"] = bench_matches([m["name"], mid], bench_entries) if bench_entries else []
    if endpoint_ids:
        extra = sorted(endpoint_ids - set(go_models))
        if extra:
            warnings.append(f"in Go endpoint but not in docs tables (no quota/privacy data): {', '.join(extra)}")

    zen_ids = set(zen_by_id) or (collect_ids(zen_rows) if zen_rows is not None else set())

    # Any opencode/ ID mentioned anywhere in models.md (roster OR a Policy allowlist) is "watched".
    md_path = Path(args.models_md)
    md_refs = extract_refs(md_path.read_text()) if md_path.exists() else []
    watched = {r.split("/", 1)[1] for r in md_refs if r.startswith("opencode/")}
    zen_models = {}
    for zid in sorted(zen_ids):
        row = zen_by_id.get(zid)
        zname = row.get("name") if isinstance(row, dict) and isinstance(row.get("name"), str) else zid
        zen_models[zid] = {
            "name": zname,
            "watched": zid in watched,
            "models_dev": row,
            "benchmarks": bench_matches([zname, zid], bench_entries, cap=4) if bench_entries else [],
        }

    refs = {}
    if md_path.exists():
        for ref in md_refs:
            provider, mid = ref.split("/", 1)
            if provider == "opencode-go":
                status = "ok" if mid in go_models else "MISSING from Go docs"
            else:
                status = "ok" if mid in zen_ids else ("MISSING from models.dev opencode list" if zen_ids else "unverified")
            entry = {"status": status}
            if provider == "opencode":
                entry["models_dev"] = zen_by_id.get(mid)
                entry["benchmarks"] = bench_matches([mid], bench_entries) if bench_entries else []
            refs[ref] = entry
    else:
        warnings.append(f"{md_path} not found; skipped reference check")

    summary = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "sources": sources,
        "warnings": warnings,
        "docs_notes": notes,
        "go_models": go_models,
        "zen_model_ids": sorted(zen_ids),
        "zen_models": zen_models,
        "models_md_refs": refs,
    }
    prev = previous_snapshot(snap_root, today)
    summary["diff"] = diff_summaries(prev, summary) if prev else {"previous_snapshot": None, "note": "first snapshot - no diff available"}

    write_json(out_dir / "summary.json", summary)
    write_json(snap_root / "latest.json", summary)
    write_json(snap_root / "latest-diff.json", summary["diff"])

    # Retention
    dirs = sorted(d for d in snap_root.iterdir() if d.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name))
    for old in dirs[: max(0, len(dirs) - args.keep)]:
        shutil.rmtree(old, ignore_errors=True)

    # Human-readable report (the scout reads this too)
    print(f"Snapshot written: {out_dir}")
    print(f"Go models parsed: {len(go_models)} | benchmark entries: {len(bench_entries)} | models.dev opencode rows: {len(zen_by_id)}")
    d = summary["diff"]
    if prev:
        print(f"Diff vs {d['previous_snapshot']}: +{len(d['added_models'])} added, -{len(d['removed_models'])} removed, {len(d['changed'])} changed (Go)")
        if d.get("zen_added") or d.get("zen_removed") or d.get("zen_changed"):
            print(f"  Zen (opencode/): +{len(d['zen_added'])} added, -{len(d['zen_removed'])} removed, {len(d['zen_changed'])} watched changed")
            for zid in d["zen_added"]:
                print(f"  + opencode/{zid}")
            for zid in d["zen_removed"]:
                print(f"  - opencode/{zid}")
            for zid in d["zen_changed"]:
                print(f"  ~ opencode/{zid}")
        for mid in d["added_models"]:
            print(f"  + {mid}")
        for mid in d["removed_models"]:
            print(f"  - {mid}")
        for mid, ch in d["changed"].items():
            print(f"  ~ {mid}: {', '.join(ch)}")
    else:
        print("First snapshot - no diff.")
    bad = {k: v["status"] for k, v in refs.items() if v["status"] != "ok"}
    for k, v in bad.items():
        print(f"  ! models.md reference {k}: {v}")
    for w in warnings:
        print(f"WARNING: {w}")
    unmatched = [mid for mid, m in go_models.items() if not m["benchmarks"]]
    if bench_entries and unmatched:
        print(f"No benchmark match for {len(unmatched)} Go models: {', '.join(unmatched)}")

    if not go_models:
        print("CRITICAL: no Go models parsed from the docs page - snapshot is unusable.", file=sys.stderr)
        return 2
    return 0


def cmd_validate(args) -> int:
    latest = Path(args.agents_dir) / "snapshots" / "latest.json"
    if not latest.exists():
        print(f"No snapshot at {latest}. Run the gather step first.", file=sys.stderr)
        return 2
    snap = json.loads(latest.read_text())
    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(snap["generated_at"])
    if age.days > 10:
        print(f"WARNING: snapshot is {age.days} days old - re-run gather first.")

    go, zen = snap["go_models"], set(snap.get("zen_model_ids", []))
    refs = extract_refs(Path(args.file).read_text())
    if not refs:
        print(f"No opencode/ or opencode-go/ model references found in {args.file}")
        return 1

    failed = False
    for ref in refs:
        provider, mid = ref.split("/", 1)
        if provider == "opencode-go":
            m = go.get(mid)
            if not m:
                print(f"MISSING  {ref}  (not in Go docs tables)")
                failed = True
                continue
            if m.get("in_endpoint") is False:
                print(f"MISSING  {ref}  (in docs but not served by the Go endpoint)")
                failed = True
                continue
            flags = []
            if m.get("privacy", {}).get("trains_on_prompts"):
                flags.append("TRAINS ON PROMPTS")
            if m.get("promo"):
                flags.append("limited-time promo")
            if m.get("free"):
                flags.append("free tier (may disappear)")
            print(f"OK       {ref}" + (f"  [{'; '.join(flags)}]" if flags else ""))
        else:
            if not zen:
                print(f"UNVERIFIED {ref}  (no models.dev opencode list in snapshot)")
            elif mid in zen:
                print(f"OK       {ref}")
            else:
                print(f"MISSING  {ref}  (not in models.dev opencode list)")
                failed = True
    return 1 if failed else 0


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("-"):
        argv = ["gather", *argv]

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gather", help="collect a new snapshot (default)")
    g.add_argument("--agents-dir", default=".agents")
    g.add_argument("--models-md", default=None, help="default: <agents-dir>/models.md")
    g.add_argument("--keep", type=int, default=12, help="snapshots to retain (default 12)")
    g.add_argument("--timeout", type=int, default=120)
    g.add_argument("--plan", choices=("go", "plus"), default=os.environ.get("OPENCODE_GO_PLAN", "go"),
                   help="which Go plan's quota tables to use (default: go, or $OPENCODE_GO_PLAN)")
    g.add_argument("--quota-multiplier", type=float, default=1.0,
                   help="scale quota figures by N when Go Plus tables can't be parsed (default 1.0)")

    v = sub.add_parser("validate", help="check every opencode/ and opencode-go/ ID in FILE exists")
    v.add_argument("file")
    v.add_argument("--agents-dir", default=".agents")

    args = p.parse_args(argv)
    if args.cmd == "gather":
        args.models_md = args.models_md or str(Path(args.agents_dir) / "models.md")
        return cmd_gather(args)
    return cmd_validate(args)


if __name__ == "__main__":
    sys.exit(main())
