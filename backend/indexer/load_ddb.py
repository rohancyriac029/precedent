"""Push the SQLite index into DynamoDB.

Stage 4B of docs/PLAN.md. Runs once per corpus commit. Nothing clones or parses
the corpus at request time.

Edge items are keyed "<src>#<dst>" so grounding is a single GetItem per edge,
which keeps audit latency independent of corpus size.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "precedent.sqlite"
MAX_EVIDENCE = 5


def stack_outputs(stack: str, region: str) -> dict[str, str]:
    cf = boto3.client("cloudformation", region_name=region)
    out = cf.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", default="precedent")
    ap.add_argument("--region", default="ap-south-1")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB_PATH.exists():
        raise SystemExit(f"no index at {DB_PATH}. Run: python tasks.py index")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    meta = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM meta")}

    # One item per service pair, carrying its count and a little evidence.
    pairs = conn.execute(
        """
        SELECT src_service, dst_service,
               COUNT(DISTINCT pattern_id) AS c,
               GROUP_CONCAT(DISTINCT pattern_id) AS pids
        FROM edges WHERE confidence='strong'
        GROUP BY src_service, dst_service
        """
    ).fetchall()

    patterns = conn.execute(
        "SELECT id, title, framework, url, services, parse_status FROM patterns"
    ).fetchall()
    titles = {p["id"]: p["title"] for p in patterns}
    urls = {p["id"]: p["url"] for p in patterns}

    edge_items = []
    for row in pairs:
        pids = [p for p in (row["pids"] or "").split(",") if p][:MAX_EVIDENCE]
        edge_items.append({
            "pair": f"{row['src_service']}#{row['dst_service']}",
            "src_service": row["src_service"],
            "dst_service": row["dst_service"],
            "count": row["c"],
            "evidence": [
                {"pattern_id": p, "title": titles.get(p, p), "url": urls.get(p, "")}
                for p in pids
            ],
        })

    pattern_items = [
        {
            "pattern_id": p["id"],
            "title": p["title"],
            "framework": p["framework"],
            "url": p["url"],
            "services": p["services"],
            "parse_status": p["parse_status"],
        }
        for p in patterns
    ]

    print(f"corpus commit : {meta.get('corpus_commit','?')[:12]}")
    print(f"edge items    : {len(edge_items)}")
    print(f"pattern items : {len(pattern_items)}")

    if args.dry_run:
        print("\ndry run, nothing written. Sample edge item:")
        print(edge_items[0] if edge_items else "(none)")
        return

    outputs = stack_outputs(args.stack, args.region)
    ddb = boto3.resource("dynamodb", region_name=args.region)

    for table_name, items, label in (
        (outputs["EdgesTableName"], edge_items, "edges"),
        (outputs["PatternsTableName"], pattern_items, "patterns"),
    ):
        table = ddb.Table(table_name)
        with table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)
        print(f"wrote {len(items):5d} {label:9s} -> {table_name}")

    print("\ndone. /corpus/stats will reflect this within a few hours "
          "(DynamoDB ItemCount is eventually consistent).")


if __name__ == "__main__":
    main()
