"""Integration rules: what AWS supports as a direct connection.

This is the half of the product that says "this will not deploy" rather than
"this is unusual". A precedent count can only ever tell you something is rare.
An UNSUPPORTED verdict is a much stronger claim, so it carries a much stronger
evidence requirement.

**Unverified rules are inert.** A row is only allowed to produce an UNSUPPORTED
verdict once it has both a documentation URL and a human's initials in
`verified_by`. Rows without initials are loaded, counted and reportable, but
they never change a label. The plan requires a person to confirm every rule
against current AWS documentation, and this makes that requirement structural
instead of a checklist item somebody forgets.

The asymmetry is deliberate: an unverified rule that says something IS supported
costs nothing if wrong, because the fallback is the corpus count. An unverified
rule that says something is NOT supported would put a false claim on screen.
"""

from __future__ import annotations

import csv
import functools
from pathlib import Path
from typing import Iterator, Optional

from core.models import RuleRef

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "integration_rules.csv"

TRUE_VALUES = {"true", "yes", "1", "y"}


class Rule:
    __slots__ = (
        "rule_id", "src_service", "dst_service", "relation_scope",
        "direct_supported", "doc_url", "verified_by", "note",
    )

    def __init__(self, row: dict[str, str]) -> None:
        self.rule_id = (row.get("rule_id") or "").strip()
        self.src_service = (row.get("src_service") or "").strip()
        self.dst_service = (row.get("dst_service") or "").strip()
        self.relation_scope = (row.get("relation_scope") or "").strip()
        self.direct_supported = (row.get("direct_supported") or "").strip().lower() in TRUE_VALUES
        self.doc_url = (row.get("doc_url") or "").strip()
        self.verified_by = (row.get("verified_by") or "").strip()
        self.note = (row.get("note") or "").strip()

    @property
    def is_verified(self) -> bool:
        """A rule may only affect a verdict once a person has checked it."""
        return bool(self.doc_url) and bool(self.verified_by)

    @property
    def pair(self) -> tuple[str, str]:
        return (self.src_service, self.dst_service)

    def as_ref(self) -> RuleRef:
        return RuleRef(rule_id=self.rule_id, doc_url=self.doc_url, note=self.note)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        state = "verified" if self.is_verified else "UNVERIFIED"
        return (f"<Rule {self.rule_id} {self.src_service}->{self.dst_service} "
                f"supported={self.direct_supported} {state}>")


class RuleTable:
    def __init__(self, rules: list[Rule]) -> None:
        self.rules = rules
        self._unsupported: dict[tuple[str, str], Rule] = {}
        self._supported: dict[tuple[str, str], Rule] = {}
        for r in rules:
            target = self._supported if r.direct_supported else self._unsupported
            target.setdefault(r.pair, r)

    # ---- the lookup grounding uses -------------------------------------

    def unsupported_rule(self, src_service: str, dst_service: str) -> Optional[RuleRef]:
        """Return a rule only when it is verified AND says the edge is unsupported."""
        rule = self._unsupported.get((src_service, dst_service))
        if rule is None or not rule.is_verified:
            return None
        return rule.as_ref()

    def supports(self, src_service: str, dst_service: str) -> Optional[bool]:
        """True, False or None when we have no rule for this pair at all."""
        if (src_service, dst_service) in self._supported:
            return True
        if (src_service, dst_service) in self._unsupported:
            return False
        return None

    def is_known_unsupported(self, src_service: str, dst_service: str) -> bool:
        """Ignores verification. Used by repair, which must not route through a
        connection we believe is impossible even if nobody has signed it off."""
        return (src_service, dst_service) in self._unsupported

    # ---- reporting ------------------------------------------------------

    @property
    def verified(self) -> list[Rule]:
        return [r for r in self.rules if r.is_verified]

    @property
    def unverified(self) -> list[Rule]:
        return [r for r in self.rules if not r.is_verified]

    def stats(self) -> dict[str, int]:
        return {
            "total": len(self.rules),
            "verified": len(self.verified),
            "unverified": len(self.unverified),
            "active_unsupported": sum(
                1 for r in self.rules if not r.direct_supported and r.is_verified
            ),
            "pending_unsupported": sum(
                1 for r in self.rules if not r.direct_supported and not r.is_verified
            ),
        }

    def problems(self) -> Iterator[str]:
        """Structural faults a human should fix before shipping."""
        seen: set[str] = set()
        for r in self.rules:
            if not r.rule_id:
                yield "a row has no rule_id"
            elif r.rule_id in seen:
                yield f"{r.rule_id}: duplicate rule_id"
            else:
                seen.add(r.rule_id)
            if not r.doc_url:
                yield f"{r.rule_id}: no doc_url, so it can never be verified"
            if not r.src_service or not r.dst_service:
                yield f"{r.rule_id}: incomplete service pair"
        contradictions = set(self._supported) & set(self._unsupported)
        for pair in sorted(contradictions):
            yield f"{pair[0]} -> {pair[1]}: listed as both supported and unsupported"

    def __len__(self) -> int:
        return len(self.rules)


def load_rules(path: str | Path | None = None) -> RuleTable:
    p = Path(path) if path else _DEFAULT_PATH
    if not p.exists():
        return RuleTable([])
    with open(p, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return RuleTable([Rule(r) for r in rows if (r.get("rule_id") or "").strip()])


@functools.lru_cache(maxsize=2)
def load(path: str | None = None) -> RuleTable:
    return load_rules(path)
