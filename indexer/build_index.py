"""Parse the whole corpus into SQLite.

Stage 3 of docs/PLAN.md. This is the offline step that turns 483 SAM/CFN
templates into the edge table every verdict counts against. It runs once per
corpus commit, in development or in CI, never at request time.

The count that matters is DISTINCT PATTERNS, not distinct edges. A pattern that
wires three Lambdas to one table is one piece of evidence for lambda -> dynamodb,
not three.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core.cfn_loader import TemplateParseError, load_file
from core.template_parser import parse_template
from core.vocabulary import load as load_vocab
from indexer.census import CORPUS, pattern_dirs, sam_cfn_templates

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "precedent.sqlite"
GITHUB_BASE = "https://github.com/aws-samples/serverless-patterns/tree"

SCHEMA = """
DROP TABLE IF EXISTS patterns;
DROP TABLE IF EXISTS edges;
DROP TABLE IF EXISTS meta;

CREATE TABLE patterns (
  id TEXT PRIMARY KEY,
  title TEXT,
  framework TEXT,
  url TEXT,
  readme TEXT,
  services TEXT,
  parse_status TEXT,
  parse_errors TEXT
);
CREATE TABLE edges (
  pattern_id TEXT,
  src_service TEXT,
  dst_service TEXT,
  relation TEXT,
  confidence TEXT,
  source_construct TEXT
);
CREATE INDEX idx_edges_pair ON edges(src_service, dst_service);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def corpus_commit() -> str:
    f = ROOT / "data" / "corpus_commit.txt"
    return f.read_text(encoding="utf-8").strip() if f.exists() else "unknown"


def pattern_title(pattern: Path) -> tuple[str, str]:
    """Title and framework from the pattern metadata, falling back to the name."""
    for name in ("pattern.json", "example-pattern.json"):
        f = pattern / name
        if not f.exists():
            continue
        try:
            meta = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        title = meta.get("title")
        framework = meta.get("framework")
        if isinstance(title, str):
            return title, framework if isinstance(framework, str) else "SAM/CFN"
    return pattern.name.replace("-", " "), "SAM/CFN"


def readme_text(pattern: Path, limit: int = 4000) -> str:
    f = pattern / "README.md"
    if not f.exists():
        return ""
    try:
        return f.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def main() -> None:
    vocab = load_vocab()
    commit = corpus_commit()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    patterns_total = 0
    patterns_parsed = 0
    edge_rows: list[tuple] = []
    pattern_rows: list[tuple] = []

    for pattern in pattern_dirs(CORPUS):
        templates = list(sam_cfn_templates(pattern))
        if not templates:
            continue
        patterns_total += 1
        pid = pattern.name
        title, framework = pattern_title(pattern)
        url = f"{GITHUB_BASE}/{commit}/{pid}"

        # One pattern can ship several templates; the union is the pattern.
        pair_rows: dict[tuple[str, str], tuple] = {}
        services: set[str] = set()
        errors: list[str] = []

        for tpl in templates:
            try:
                loaded = load_file(tpl)
            except TemplateParseError as exc:
                errors.append(f"{tpl.name}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{tpl.name}: {type(exc).__name__}")
                continue
            try:
                g = parse_template(loaded, vocab).graph
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{tpl.name}: parse {type(exc).__name__}")
                continue

            for n in g.nodes:
                services.add(n.service)
            for e in g.edges:
                a, b = g.service_of(e.src), g.service_of(e.dst)
                if a == b or a == "unknown" or b == "unknown":
                    continue
                # keep the strongest evidence for a pair within this pattern
                prev = pair_rows.get((a, b))
                if prev is None or (prev[4] == "weak" and e.confidence == "strong"):
                    pair_rows[(a, b)] = (
                        pid, a, b, e.relation, e.confidence, e.source_construct or "",
                    )

        status = "ok" if pair_rows else ("error" if errors else "no_edges")
        if pair_rows:
            patterns_parsed += 1
        edge_rows.extend(pair_rows.values())
        pattern_rows.append(
            (pid, title, framework, url, readme_text(pattern),
             ",".join(sorted(services)), status, "; ".join(errors[:3]))
        )

    conn.executemany("INSERT INTO patterns VALUES (?,?,?,?,?,?,?,?)", pattern_rows)
    conn.executemany("INSERT INTO edges VALUES (?,?,?,?,?,?)", edge_rows)
    coverage = patterns_parsed / patterns_total if patterns_total else 0.0
    conn.executemany(
        "INSERT INTO meta VALUES (?,?)",
        [
            ("corpus_commit", commit),
            ("patterns_total", str(patterns_total)),
            ("patterns_parsed", str(patterns_parsed)),
            ("parse_coverage", f"{coverage:.4f}"),
            ("built_at", __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()),
        ],
    )
    conn.commit()

    pairs = conn.execute(
        "SELECT src_service, dst_service, COUNT(DISTINCT pattern_id) c "
        "FROM edges WHERE confidence='strong' GROUP BY 1,2 ORDER BY c DESC"
    ).fetchall()

    print(f"corpus commit    : {commit[:12]}")
    print(f"patterns indexed : {patterns_total}")
    print(f"  with >=1 edge  : {patterns_parsed}  ({coverage:.1%})")
    print(f"edge rows        : {len(edge_rows)}")
    print(f"distinct pairs   : {len(pairs)}")
    print(f"\ntop 15 pairs:")
    for a, b, c in pairs[:15]:
        print(f"  {a:22s} -> {b:22s} {c}")
    print(f"\nwritten to {DB_PATH.relative_to(ROOT)}")
    # A compact pattern index, bundled into the Lambda package.
    # The DynamoDB edge table is keyed by pair and keeps only five evidence ids,
    # so pattern -> edges cannot be rebuilt from it. This file can, it is small,
    # and shipping it beats an S3 round trip on every cold start.
    titles = {r[0]: r[1] for r in pattern_rows}
    urls = {r[0]: r[3] for r in pattern_rows}
    by_pattern: dict[str, list[list[str]]] = {}
    for pid, a, b in conn.execute(
        "SELECT DISTINCT pattern_id, src_service, dst_service FROM edges "
        "WHERE confidence='strong'"
    ):
        by_pattern.setdefault(pid, []).append([a, b])
    index_payload = {
        "corpus_commit": commit,
        "patterns": {
            pid: {"edges": e, "title": titles.get(pid, pid), "url": urls.get(pid, "")}
            for pid, e in by_pattern.items()
        },
    }
    index_path = ROOT / "data" / "pattern_index.json"
    index_path.write_text(json.dumps(index_payload, separators=(",", ":")), encoding="utf-8")
    print("pattern index    : %d patterns, %.0f KB -> %s" % (
        len(by_pattern), index_path.stat().st_size / 1024, index_path.relative_to(ROOT)))
    conn.close()


if __name__ == "__main__":
    main()
