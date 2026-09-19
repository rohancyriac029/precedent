"""Evidence retrieval over pattern README passages. BM25, no embeddings.

Rule 5 of CLAUDE.md: no embeddings in the Lambda runtime, retrieval uses BM25.

The important decision is not the scoring function, it is the candidate set.
A question about an audit is answered only from the patterns that audit already
cites: the evidence behind each verdict, the patterns along each grounded
repair route, and the closest reference patterns. Those were chosen by the
deterministic core, so the model never decides what counts as relevant, and
it cannot pull in a pattern the audit did not already point at.

BM25 then ranks passages *within* that set. IDF comes from the whole passage
collection, so a word every README uses ("Lambda", "template") is weighted
down even when the candidate set is small.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

DATA = Path(__file__).resolve().parent.parent / "data"
CHUNKS_PATH = DATA / "readme_chunks.json"

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be by can for from has have how i if in into is it its of on or "
    "that the their then there these this to use used uses using was we what when where "
    "which why will with you your does do not no my our me".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


@dataclass(frozen=True)
class Passage:
    pattern_id: str
    index: int      # position within that pattern's README passages
    text: str
    score: float


class Bm25:
    """Okapi BM25 over a fixed passage collection."""

    def __init__(self, chunks: dict[str, list[str]], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs: list[tuple[str, int, str, Counter, int]] = []
        df: Counter = Counter()
        for pid in sorted(chunks):
            for i, text in enumerate(chunks[pid]):
                toks = tokenize(text)
                tf = Counter(toks)
                self.docs.append((pid, i, text, tf, len(toks)))
                df.update(tf.keys())
        n = len(self.docs) or 1
        self.avgdl = sum(d[4] for d in self.docs) / n
        # The +1 inside the log keeps IDF positive for very common terms.
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def _score(self, query: list[str], tf: Counter, dl: int) -> float:
        s = 0.0
        for t in query:
            f = tf.get(t)
            if not f:
                continue
            norm = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
            s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / norm
        return s

    def search(self, query: str, pattern_ids: Iterable[str], *, k: int = 6,
               per_pattern: int = 2, max_chars: int = 3000) -> list[Passage]:
        """Top passages from the candidate patterns only.

        Deterministic: ties break on pattern id and then passage position.
        At most `per_pattern` passages from one pattern, so one long README
        cannot crowd out the rest of the evidence.
        """
        allowed = set(pattern_ids)
        q = tokenize(query)
        scored = [
            Passage(pid, i, text, round(self._score(q, tf, dl), 6))
            for pid, i, text, tf, dl in self.docs
            if pid in allowed
        ]
        scored.sort(key=lambda p: (-p.score, p.pattern_id, p.index))

        out: list[Passage] = []
        taken: Counter = Counter()
        used = 0
        for p in scored:
            if len(out) >= k or p.score <= 0:
                break   # a passage sharing no term with the query is not evidence
            if taken[p.pattern_id] >= per_pattern:
                continue
            if used + len(p.text) > max_chars and out:
                continue
            out.append(p)
            taken[p.pattern_id] += 1
            used += len(p.text)
        return out


@lru_cache(maxsize=1)
def load(path: Optional[str] = None) -> Optional[Bm25]:
    """The bundled passage index, or None when the file is absent."""
    f = Path(path) if path else CHUNKS_PATH
    if not f.exists():
        return None
    return Bm25(json.loads(f.read_text(encoding="utf-8")))


def cited_patterns(report: dict) -> list[str]:
    """Every pattern id the deterministic audit points at, in a stable order.

    This is the whole candidate set for retrieval. Evidence first, then the
    patterns along repair routes, then the closest reference patterns.
    """
    seen: dict[str, None] = {}
    for v in report.get("edges") or []:
        for e in v.get("evidence") or []:
            seen.setdefault(e.get("pattern_id", ""), None)
        for route in v.get("repairs") or []:
            for hop in route.get("hop_evidence") or []:
                for e in hop:
                    seen.setdefault(e.get("pattern_id", ""), None)
    for p in report.get("closest_patterns") or []:
        seen.setdefault(p.get("pattern_id", ""), None)
    return [pid for pid in seen if pid]
