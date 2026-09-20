"""Map free text to a canonical service id.

Used by the Mermaid parser, where a node is labelled by a human ("Upload
Bucket", "Order Queue"), and as a safety net after model extraction.

Three passes, in descending confidence:

1. exact alias match after normalisation
2. alias contained in the label, longest alias first
3. fuzzy match over the alias list, above a threshold

Embeddings were the original plan for step 3. They are gone: the Bedrock
endpoint available to us serves no embedding model, and sentence-transformers
drags in torch, which does not fit a zip-packaged Lambda. `difflib` is stdlib,
deterministic and fast enough, and a canonicalisation we cannot reproduce is
worthless to a product whose whole claim is reproducibility.

Anything below the threshold becomes "unknown". Guessing a service would
silently attach a real precedent count to the wrong thing.
"""

from __future__ import annotations

import csv
import functools
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from core.vocabulary import UNKNOWN, Vocabulary, load as load_vocab

_ALIAS_PATH = Path(__file__).resolve().parent.parent / "data" / "aliases.csv"

FUZZY_THRESHOLD = 0.86

# Words that say nothing about which service something is.
_NOISE = re.compile(
    r"\b(aws|amazon|the|a|an|my|our|new|main|primary|prod|production|dev|test|"
    r"staging|service|resource|instance)\b"
)
_PUNCT = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Lowercase, strip punctuation, drop filler words, collapse whitespace."""
    s = text.lower()
    s = _PUNCT.sub(" ", s)
    s = _NOISE.sub(" ", s)
    return _SPACES.sub(" ", s).strip()


class Canonicalizer:
    def __init__(self, aliases: dict[str, str], vocab: Vocabulary):
        self._aliases = aliases
        self._vocab = vocab
        # longest first, so "kinesis firehose" wins over "kinesis"
        self._by_length = sorted(aliases, key=len, reverse=True)

    def resolve(self, label: str) -> tuple[str, str, Optional[float]]:
        """Return (service, method, score). service is UNKNOWN when unsure."""
        if not label or not label.strip():
            return UNKNOWN, "unknown", None

        text = normalise(label)
        if not text:
            return UNKNOWN, "unknown", None

        # 1. exact
        hit = self._aliases.get(text)
        if hit:
            return hit, "alias", 1.0

        # 2. containment, longest alias first. The match is on whole words (the
        # padding), so a two-letter alias like "s3" is safe: "S3 Archive" is a
        # bucket, and "s3" cannot match inside another word.
        padded = f" {text} "
        for alias in self._by_length:
            if len(alias) < 2:
                continue
            if f" {alias} " in padded:
                return self._aliases[alias], "alias", 0.95

        # 3. fuzzy
        best_alias, best_score = None, 0.0
        for alias in self._aliases:
            score = SequenceMatcher(None, text, alias).ratio()
            if score > best_score:
                best_alias, best_score = alias, score
        if best_alias is not None and best_score >= FUZZY_THRESHOLD:
            return self._aliases[best_alias], "fuzzy", round(best_score, 3)

        return UNKNOWN, "unknown", round(best_score, 3) if best_alias else None


def load_aliases(path: str | Path | None = None) -> dict[str, str]:
    p = Path(path) if path else _ALIAS_PATH
    out: dict[str, str] = {}
    if not p.exists():
        return out
    with open(p, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            alias = normalise(row.get("alias") or "")
            service = (row.get("service") or "").strip()
            if alias and service:
                out.setdefault(alias, service)
    return out


@functools.lru_cache(maxsize=2)
def load(path: str | None = None) -> Canonicalizer:
    vocab = load_vocab()
    aliases = load_aliases(path)
    # A service id is always an alias for itself.
    for sid in vocab.ids:
        aliases.setdefault(normalise(sid.replace("_", " ")), sid)
        aliases.setdefault(normalise(vocab.title(sid)), sid)
    return Canonicalizer(aliases, vocab)
