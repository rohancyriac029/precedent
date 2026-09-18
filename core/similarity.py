"""Closest existing patterns.

Section 4 of docs/PLAN.md.

    idf(e)            = log(1 + N / (1 + df(e)))      # smoothed, never zero
    containment(A, P) = sum(idf(e) for e in A n P) / sum(idf(e) for e in P)

Containment, not Jaccard. Corpus patterns are small, usually two to four
services, while a user architecture is much larger. Jaccard would punish every
pattern for being small and rank nothing usefully. Containment asks a better
question: how much of THIS pattern does the user's design already contain?

IDF weighting matters just as much. `apigateway -> lambda` appears in 101
patterns, so sharing it says almost nothing. `firehose -> s3` appears in 7, so
sharing it is real evidence. Unweighted overlap would rank every API-and-Lambda
pattern as equally close to everything.

This turns criticism into a suggestion: not just "your edge is unprecedented"
but "here is the closest thing that does exist".
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Iterable, Mapping, Optional

from core.models import ArchitectureGraph, ClosestPattern

DEFAULT_LIMIT = 5
MIN_SHARED_EDGES = 1


class PatternIndex:
    """Precomputed IDF over the corpus. Build once, query many times."""

    def __init__(
        self,
        pattern_edges: Mapping[str, set[tuple[str, str]]],
        meta: Optional[Mapping[str, dict]] = None,
    ):
        self.pattern_edges = {p: set(e) for p, e in pattern_edges.items() if e}
        self.meta = meta or {}
        self.n = len(self.pattern_edges)

        df: Counter = Counter()
        for edges in self.pattern_edges.values():
            for edge in edges:
                df[edge] += 1
        self.df = df
        # Smoothed IDF: log(1 + N/(1+df)), never zero and never negative.
        #
        # The textbook log(N/(1+df)) hits exactly 0 for a near-ubiquitous edge.
        # apigateway -> lambda appears in 101 of our patterns, so it would weigh
        # nothing, and any pattern whose only edge is that one would have a zero
        # denominator and be dropped from the ranking entirely. A user whose
        # design IS an API and a Lambda would then match nothing at all.
        # Smoothing keeps common edges cheap without making them free.
        self.idf = {
            edge: math.log(1 + self.n / (1 + count)) for edge, count in df.items()
        }

    def weight(self, edge: tuple[str, str]) -> float:
        """An edge the corpus has never seen weighs as much as a once-seen one."""
        if edge in self.idf:
            return self.idf[edge]
        return math.log(1 + self.n / 2.0) if self.n else 0.0

    def closest(
        self,
        edges: Iterable[tuple[str, str]],
        *,
        limit: int = DEFAULT_LIMIT,
        min_shared: int = MIN_SHARED_EDGES,
        exclude: Optional[set[str]] = None,
    ) -> list[ClosestPattern]:
        target = {e for e in edges if e[0] != e[1]}
        if not target or not self.n:
            return []

        exclude = exclude or set()
        scored: list[tuple[float, float, int, str, list[tuple[str, str]]]] = []

        for pattern_id, pattern in self.pattern_edges.items():
            if pattern_id in exclude:
                continue
            shared = target & pattern
            if len(shared) < min_shared:
                continue
            denominator = sum(self.weight(e) for e in pattern)
            if denominator <= 0:
                continue
            numerator = sum(self.weight(e) for e in shared)
            scored.append((
                numerator / denominator,   # containment
                numerator,                 # how informative the overlap is
                len(shared),
                pattern_id,
                sorted(shared),
            ))

        # Containment first. Ties break on the IDF mass of what is shared, not
        # on how many edges matched: a pattern that shares one rare edge tells
        # you more than one that shares a single ubiquitous edge, and both score
        # 1.0 on containment. Pattern id last, so the ordering is total and the
        # report stays reproducible.
        scored.sort(key=lambda row: (-row[0], -row[1], -row[2], row[3]))

        out: list[ClosestPattern] = []
        for containment, _mass, _n_shared, pattern_id, shared in scored[:limit]:
            info = self.meta.get(pattern_id, {})
            out.append(ClosestPattern(
                pattern_id=pattern_id,
                title=info.get("title") or pattern_id,
                url=info.get("url") or "",
                containment=round(containment, 4),
                shared_edges=shared,
            ))
        return out


def closest_patterns(
    graph: ArchitectureGraph,
    index: PatternIndex,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[ClosestPattern]:
    return index.closest(graph.service_edges(), limit=limit)


def build_index_from_store(store) -> PatternIndex:
    """Build from a SqliteStore. Used offline and by evaluation."""
    return PatternIndex(store.pattern_edges(), store.pattern_meta())


def load_index(path=None) -> Optional["PatternIndex"]:
    """Load the bundled index written by `indexer/build_index.py`.

    Returns None when the file is absent, so closest-patterns degrades to an
    empty list rather than failing an audit.
    """
    import json
    from pathlib import Path

    p = Path(path) if path else Path(__file__).resolve().parent.parent / "data" / "pattern_index.json"
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    patterns = payload.get("patterns") or {}
    edges = {pid: {(a, b) for a, b in spec.get("edges", [])} for pid, spec in patterns.items()}
    meta = {pid: {"title": spec.get("title", pid), "url": spec.get("url", "")}
            for pid, spec in patterns.items()}
    return PatternIndex(edges, meta)
