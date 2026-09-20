"""Where corpus facts come from.

One interface, three implementations:

* `SqliteStore`   — the local index. Used by evaluation, by the indexer and by
                    anything that needs to run the real pipeline offline.
* `DynamoStore`   — the deployed path. One GetItem per edge.
* `DictStore`     — in-memory, for tests.

The point of the interface is that `core/` never knows which one it has. The
evaluation runs in Stage 8 need the real corpus, repeatedly, without calling
AWS; before this existed the only real store lived inside the Lambda handler,
so nothing could be measured offline.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Optional, Protocol

from core.grounding import EdgeFacts
from core.models import Evidence

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "precedent.sqlite"
MAX_EVIDENCE = 5


class EdgeStore(Protocol):
    def facts(self, src_service: str, dst_service: str) -> EdgeFacts: ...
    def all_pairs(self) -> list[tuple[str, str, int]]: ...


# --------------------------------------------------------------------------
# SQLite
# --------------------------------------------------------------------------


class SqliteStore:
    """Reads the index built by `indexer/build_index.py`.

    Counts are DISTINCT PATTERNS, not rows. A pattern wiring three Lambdas to
    one table is one piece of evidence for lambda -> dynamodb, not three.
    """

    def __init__(self, path: str | Path = DEFAULT_DB):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(
                f"no index at {self.path}. Run: python tasks.py index"
            )
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._cache: dict[tuple[str, str], EdgeFacts] = {}

    # -- EdgeStore ------------------------------------------------------

    def facts(self, src_service: str, dst_service: str) -> EdgeFacts:
        key = (src_service, dst_service)
        if key in self._cache:
            return self._cache[key]
        rows = self._conn.execute(
            """
            SELECT DISTINCT e.pattern_id, p.title, p.url
            FROM edges e LEFT JOIN patterns p ON p.id = e.pattern_id
            WHERE e.src_service = ? AND e.dst_service = ? AND e.confidence = 'strong'
            ORDER BY e.pattern_id
            """,
            (src_service, dst_service),
        ).fetchall()
        facts = EdgeFacts(
            count=len(rows),
            evidence=tuple(
                Evidence(pattern_id=r["pattern_id"],
                         title=r["title"] or r["pattern_id"],
                         url=r["url"] or "")
                for r in rows[:MAX_EVIDENCE]
            ),
        )
        self._cache[key] = facts
        return facts

    def all_pairs(self) -> list[tuple[str, str, int]]:
        rows = self._conn.execute(
            """
            SELECT src_service, dst_service, COUNT(DISTINCT pattern_id) AS c
            FROM edges WHERE confidence = 'strong'
            GROUP BY src_service, dst_service
            """
        ).fetchall()
        return [(r["src_service"], r["dst_service"], r["c"]) for r in rows]

    # -- extras the deployed store cannot offer --------------------------

    def meta(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self._conn.execute("SELECT * FROM meta")}

    def corpus_commit(self) -> str:
        return self.meta().get("corpus_commit", "unknown")

    def pattern(self, pattern_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT id, title, framework, url, services, parse_status FROM patterns WHERE id = ?",
            (pattern_id,),
        ).fetchone()
        return dict(row) if row else None

    def pattern_edges(self) -> dict[str, set[tuple[str, str]]]:
        """pattern_id -> set of its service edges. Used by closest-pattern ranking."""
        out: dict[str, set[tuple[str, str]]] = {}
        for r in self._conn.execute(
            "SELECT pattern_id, src_service, dst_service FROM edges WHERE confidence='strong'"
        ):
            out.setdefault(r["pattern_id"], set()).add(
                (r["src_service"], r["dst_service"])
            )
        return out

    def pattern_meta(self) -> dict[str, dict]:
        return {
            r["id"]: {"title": r["title"], "url": r["url"]}
            for r in self._conn.execute("SELECT id, title, url FROM patterns")
        }

    def close(self) -> None:
        self._conn.close()


# --------------------------------------------------------------------------
# DynamoDB
# --------------------------------------------------------------------------


class DynamoStore:
    """The deployed path. Lives here rather than in the handler so both stores
    sit behind the same interface and the handler stays thin."""

    def __init__(self, table_name: str, resource=None):
        import boto3  # imported lazily: local runs must not need boto3

        self._ddb = resource or boto3.resource("dynamodb")
        self._table = self._ddb.Table(table_name) if table_name else None
        self._cache: dict[tuple[str, str], EdgeFacts] = {}

    def facts(self, src_service: str, dst_service: str) -> EdgeFacts:
        key = (src_service, dst_service)
        if key in self._cache:
            return self._cache[key]
        facts = EdgeFacts()
        if self._table is not None:
            try:
                item = self._table.get_item(
                    Key={"pair": f"{src_service}#{dst_service}"}
                ).get("Item")
                if item:
                    facts = EdgeFacts(
                        count=int(item.get("count", 0)),
                        evidence=tuple(
                            Evidence(pattern_id=e.get("pattern_id", ""),
                                     title=e.get("title", ""),
                                     url=e.get("url", ""))
                            for e in item.get("evidence", [])
                        ),
                    )
            except Exception:  # a lookup failure must degrade, never fail the audit
                pass
        self._cache[key] = facts
        return facts

    def all_pairs(self) -> list[tuple[str, str, int]]:
        pairs: list[tuple[str, str, int]] = []
        if self._table is None:
            return pairs
        kwargs = {
            "ProjectionExpression": "src_service, dst_service, #c",
            "ExpressionAttributeNames": {"#c": "count"},
        }
        while True:
            page = self._table.scan(**kwargs)
            for item in page.get("Items", []):
                pairs.append((item["src_service"], item["dst_service"],
                              int(item.get("count", 0))))
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        return pairs


# --------------------------------------------------------------------------
# In-memory
# --------------------------------------------------------------------------


class DictStore:
    def __init__(self, counts: dict[tuple[str, str], int],
                 evidence: Optional[dict[tuple[str, str], list[Evidence]]] = None):
        self._counts = counts
        self._evidence = evidence or {}

    def facts(self, src_service: str, dst_service: str) -> EdgeFacts:
        key = (src_service, dst_service)
        return EdgeFacts(
            count=self._counts.get(key, 0),
            evidence=tuple(self._evidence.get(key, [])[:MAX_EVIDENCE]),
        )

    def all_pairs(self) -> list[tuple[str, str, int]]:
        return [(a, b, c) for (a, b), c in self._counts.items()]


def open_store(path: str | Path | None = None) -> SqliteStore:
    """Convenience for scripts and evaluation runners."""
    return SqliteStore(path or DEFAULT_DB)
