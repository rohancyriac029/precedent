"""Data contracts for Precedent.

Section 6 of docs/PLAN.md. These schemas are the boundary between every stage:
parsers produce ArchitectureGraph, the audit pipeline produces AuditReport, and the
frontend works against fixtures generated from these until the API exists.

This module must never import from extract/llm/. See CLAUDE.md rule 1.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Vocabularies (closed sets, used as JSON-schema enums for extraction)
# --------------------------------------------------------------------------

Relation = Literal[
    "triggers",
    "invokes",
    "reads",
    "writes",
    "publishes",
    "sends",
    "starts_execution",
    "subscribes",
    "flows_to",
    "configured_with",
]

EdgeLabel = Literal[
    "GROUNDED",
    "RARE",
    "UNPRECEDENTED_IN_CORPUS",
    "UNSUPPORTED",
    "UNKNOWN_SERVICE",
]

NodeFlag = Literal["ORPHAN", "OVERLAPPING_CAPABILITY"]

# "fuzzy" replaces the original "embedding": embeddings are cut from the runtime.
# See section 3.1 of the plan.
CanonMethod = Literal[
    "resource_type",
    "alias",
    "fuzzy",
    "llm",
    "user",
    "unknown",
]

InputType = Literal["template", "mermaid", "prose"]
Confidence = Literal["strong", "weak"]


# --------------------------------------------------------------------------
# Graph
# --------------------------------------------------------------------------


class Node(BaseModel):
    id: str
    label: str
    service: str = "unknown"
    canon_method: CanonMethod = "unknown"
    canon_score: Optional[float] = None
    source_ref: Optional[str] = None  # logical ID, or mermaid node id

    def is_known(self) -> bool:
        return self.service != "unknown"


class Edge(BaseModel):
    src: str  # node id, not service id
    dst: str
    relation: Relation = "flows_to"
    confidence: Confidence = "strong"
    source_construct: Optional[str] = None  # e.g. "SAM.Function.Events.S3"

    def pair(self) -> tuple[str, str]:
        return (self.src, self.dst)


class ArchitectureGraph(BaseModel):
    input_type: InputType
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def node_by_id(self, node_id: str) -> Optional[Node]:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def service_of(self, node_id: str) -> str:
        n = self.node_by_id(node_id)
        return n.service if n else "unknown"

    def service_edges(self) -> set[tuple[str, str]]:
        """Distinct (src_service, dst_service) pairs, self-loops removed.

        This is the unit that grounding, evaluation and labelling all agree on.
        """
        out: set[tuple[str, str]] = set()
        for e in self.edges:
            a, b = self.service_of(e.src), self.service_of(e.dst)
            if a != b:
                out.add((a, b))
        return out


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------


class Evidence(BaseModel):
    pattern_id: str
    title: str
    url: str


class RuleRef(BaseModel):
    rule_id: str
    doc_url: str
    note: str = ""


class RepairPath(BaseModel):
    services: list[str]
    hop_counts: list[int] = Field(default_factory=list)
    hop_evidence: list[list[Evidence]] = Field(default_factory=list)
    total_weight: float = 0.0

    @property
    def hops(self) -> int:
        return max(len(self.services) - 1, 0)


class EdgeVerdict(BaseModel):
    src_service: str
    dst_service: str
    edge: Edge
    label: EdgeLabel
    count: int = 0
    evidence: list[Evidence] = Field(default_factory=list, max_length=5)
    rule: Optional[RuleRef] = None
    repairs: list[RepairPath] = Field(default_factory=list)


class NodeVerdict(BaseModel):
    node_id: str
    flags: list[NodeFlag] = Field(default_factory=list)
    note: Optional[str] = None


class ClosestPattern(BaseModel):
    pattern_id: str
    title: str
    url: str
    containment: float
    shared_edges: list[tuple[str, str]] = Field(default_factory=list)


class CorpusInfo(BaseModel):
    commit: str
    patterns_total: int = 0
    patterns_parsed: int = 0
    parse_coverage: float = 0.0


class AuditReport(BaseModel):
    audit_id: str
    corpus: CorpusInfo
    graph: ArchitectureGraph
    edges: list[EdgeVerdict] = Field(default_factory=list)
    nodes: list[NodeVerdict] = Field(default_factory=list)
    closest_patterns: list[ClosestPattern] = Field(default_factory=list)
    summary: str = ""
    narrative: Optional[str] = None
    limitations: list[str] = Field(default_factory=list)
    timings_ms: dict[str, int] = Field(default_factory=dict)

    def deterministic_fingerprint(self) -> str:
        """Stable hash of everything that must not change between identical runs.

        Excludes audit_id, timings_ms and narrative. E6 in the plan asserts that
        five runs of the same input over the same corpus commit produce the same
        value here.
        """
        import hashlib

        payload = self.model_dump(
            mode="json",
            exclude={"audit_id", "timings_ms", "narrative"},
        )
        blob = _canonical_json(payload)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _canonical_json(obj: object) -> str:
    import json

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
