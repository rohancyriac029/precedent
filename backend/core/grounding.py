"""Edge grounding: turn an architecture graph into verdicts.

Section 4 of docs/PLAN.md.

    count(src, dst) = distinct corpus patterns containing that service edge
    label = UNSUPPORTED             if the rules table says the direct edge is not supported
          = UNKNOWN_SERVICE         if either endpoint is outside the vocabulary
          = GROUNDED                if count >= GROUNDED_MIN
          = RARE                    if RARE_MIN <= count < GROUNDED_MIN
          = UNPRECEDENTED_IN_CORPUS otherwise

Every label here is computed, never inferred by a model, and depends only on the
input graph and the pinned corpus commit. That is what makes a report
reproducible, and it is the whole reason the core never calls an LLM.

One honesty rule is baked in: absence from the corpus is not evidence of
impossibility. The label is UNPRECEDENTED_IN_CORPUS, never "novel" and never
"unsupported", unless a doc-cited rule says otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from core.models import (
    ArchitectureGraph,
    Edge,
    EdgeLabel,
    EdgeVerdict,
    Evidence,
    NodeVerdict,
    RuleRef,
)
from core.vocabulary import Vocabulary, load as load_vocab

DEFAULT_GROUNDED_MIN = 3
DEFAULT_RARE_MIN = 1
MAX_EVIDENCE = 5


@dataclass(frozen=True)
class EdgeFacts:
    """What the corpus knows about one service pair."""

    count: int = 0
    evidence: tuple[Evidence, ...] = ()


class EdgeStore(Protocol):
    """Where corpus counts come from. SQLite locally, DynamoDB deployed."""

    def facts(self, src_service: str, dst_service: str) -> EdgeFacts: ...


class DictEdgeStore:
    """In-memory store, used by tests and by the offline evaluation runs."""

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


RuleLookup = Callable[[str, str], Optional[RuleRef]]


def _no_rules(_src: str, _dst: str) -> Optional[RuleRef]:
    return None


def label_edge(
    src_service: str,
    dst_service: str,
    facts: EdgeFacts,
    vocab: Vocabulary,
    rule: Optional[RuleRef] = None,
    *,
    grounded_min: int = DEFAULT_GROUNDED_MIN,
    rare_min: int = DEFAULT_RARE_MIN,
) -> EdgeLabel:
    """Pure function. Same inputs, same label, every time."""
    if not vocab.has(src_service) or not vocab.has(dst_service):
        return "UNKNOWN_SERVICE"
    if rule is not None:
        return "UNSUPPORTED"
    if facts.count >= grounded_min:
        return "GROUNDED"
    if facts.count >= rare_min:
        return "RARE"
    return "UNPRECEDENTED_IN_CORPUS"


def ground_graph(
    graph: ArchitectureGraph,
    store: EdgeStore,
    vocab: Optional[Vocabulary] = None,
    rule_lookup: RuleLookup = _no_rules,
    *,
    grounded_min: int = DEFAULT_GROUNDED_MIN,
    rare_min: int = DEFAULT_RARE_MIN,
    include_weak: bool = False,
) -> list[EdgeVerdict]:
    """One verdict per distinct service-level edge in the graph.

    Weak edges (environment-variable references and the like) are excluded by
    default: they indicate configuration, not a data or control-flow
    connection, and counting them would inflate precedent.
    """
    vocab = vocab or load_vocab()
    seen: set[tuple[str, str]] = set()
    verdicts: list[EdgeVerdict] = []

    for edge in graph.edges:
        if edge.confidence == "weak" and not include_weak:
            continue
        src = graph.service_of(edge.src)
        dst = graph.service_of(edge.dst)
        if src == dst:
            continue
        if (src, dst) in seen:
            continue
        seen.add((src, dst))

        facts = store.facts(src, dst)
        rule = rule_lookup(src, dst)
        # An SDK-capable compute service can call any AWS API, so a rule saying
        # a direct event integration is unsupported does not apply to it.
        if rule is not None and src in vocab.sdk_capable:
            rule = None

        verdicts.append(
            EdgeVerdict(
                src_service=src,
                dst_service=dst,
                edge=edge,
                label=label_edge(src, dst, facts, vocab, rule,
                                 grounded_min=grounded_min, rare_min=rare_min),
                count=facts.count,
                evidence=list(facts.evidence),
                rule=rule,
            )
        )
    return sorted(verdicts, key=lambda v: (v.src_service, v.dst_service))


def check_nodes(graph: ArchitectureGraph, vocab: Optional[Vocabulary] = None) -> list[NodeVerdict]:
    """Orphan and overlapping-capability flags.

    Overlap is reported as a question, never as a verdict. Two queues with the
    same producer and the same consumer may well be deliberate.
    """
    vocab = vocab or load_vocab()
    upstream: dict[str, set[str]] = {n.id: set() for n in graph.nodes}
    downstream: dict[str, set[str]] = {n.id: set() for n in graph.nodes}
    for e in graph.edges:
        if e.src in downstream:
            downstream[e.src].add(e.dst)
        if e.dst in upstream:
            upstream[e.dst].add(e.src)

    verdicts: list[NodeVerdict] = []
    for node in graph.nodes:
        flags = []
        note = None
        if not upstream[node.id] and not downstream[node.id]:
            flags.append("ORPHAN")
            note = "Nothing connects to this component."

        cls = vocab.capability_class_of(node.service)
        if cls:
            for other in graph.nodes:
                if other.id == node.id or other.service == node.service:
                    continue
                if vocab.capability_class_of(other.service) != cls:
                    continue
                if upstream[node.id] & upstream[other.id] and \
                        downstream[node.id] & downstream[other.id]:
                    flags.append("OVERLAPPING_CAPABILITY")
                    note = (
                        f"{vocab.title(node.service)} and {vocab.title(other.service)} "
                        f"share a producer and a consumer. Do you need both?"
                    )
                    break
        if flags:
            verdicts.append(NodeVerdict(node_id=node.id, flags=flags, note=note))
    return verdicts


def summarize(verdicts: list[EdgeVerdict], nodes: list[NodeVerdict]) -> str:
    """Deterministic summary text. No model involved.

    Deliberately says "no precedent in this corpus" rather than anything that
    could be read as "novel" or "impossible".
    """
    if not verdicts:
        return "No service-to-service connections were found in this input."

    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.label] = counts.get(v.label, 0) + 1
    total = len(verdicts)

    # Singular and plural verb forms. This sentence is the headline of every
    # report, and "1 have no precedent" reads as broken.
    def n(count: int, one: str, many: str) -> str:
        return f"{count} {one if count == 1 else many}"

    grounded = counts.get("GROUNDED", 0)
    parts = [
        f"{grounded} of {total} connection{'' if total == 1 else 's'} "
        f"{'is' if grounded == 1 else 'are'} well precedented"
    ]
    if counts.get("RARE"):
        parts.append(n(counts["RARE"], "appears in only one or two patterns",
                       "appear in only one or two patterns"))
    if counts.get("UNSUPPORTED"):
        unsupported = [v for v in verdicts if v.label == "UNSUPPORTED"]
        names = ", ".join(f"{v.src_service} to {v.dst_service}" for v in unsupported[:2])
        parts.append(n(len(unsupported), "is not supported as a direct integration",
                       "are not supported as a direct integration") + f" ({names})")
    if counts.get("UNPRECEDENTED_IN_CORPUS"):
        parts.append(n(counts["UNPRECEDENTED_IN_CORPUS"], "has no precedent in this corpus",
                       "have no precedent in this corpus"))
    if counts.get("UNKNOWN_SERVICE"):
        parts.append(n(counts["UNKNOWN_SERVICE"], "involves a service outside our vocabulary",
                       "involve a service outside our vocabulary"))

    overlaps = sum(1 for n_ in nodes if "OVERLAPPING_CAPABILITY" in n_.flags)
    orphans = sum(1 for n_ in nodes if "ORPHAN" in n_.flags)
    if overlaps:
        parts.append(n(overlaps, "component may overlap in capability",
                       "components may overlap in capability"))
    if orphans:
        parts.append(n(orphans, "component is not connected to anything",
                       "components are not connected to anything"))

    return "; ".join(parts) + "."


LIMITATIONS = [
    "Absence from the corpus is not evidence that a connection is impossible.",
    "The corpus is the AWS Serverless Patterns Collection at one pinned commit, "
    "not the whole of AWS.",
    "Counts come from templates we could parse. Parse coverage is reported in /corpus/stats.",
    "Weak edges, such as environment-variable references, are excluded from counts.",
]
