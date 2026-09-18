"""The whole deterministic pipeline, in one place.

The Lambda handler is deliberately thin: it does HTTP, and calls this. That
keeps the pipeline testable without deploying anything, and keeps the rule in
CLAUDE.md honest, because nothing here can reach an LLM.

    input -> graph -> ground -> rules -> repair -> report
"""

from __future__ import annotations

import uuid
from typing import Optional

import networkx as nx

from core.cfn_loader import loads as load_template
from core.grounding import (
    LIMITATIONS,
    EdgeStore,
    check_nodes,
    ground_graph,
    summarize,
)
from core.mermaid_parser import parse_mermaid
from core.models import ArchitectureGraph, AuditReport, CorpusInfo, EdgeVerdict
from core.repair import build_corpus_graph, find_repairs
from core.rules import RuleTable, load as load_rules
from core.similarity import PatternIndex, closest_patterns
from core.template_parser import parse_template
from core.vocabulary import Vocabulary, load as load_vocab

MAX_INPUT_BYTES = 256 * 1024

# Labels worth offering a repair for. RARE is included because "this works but
# almost nobody does it" is exactly when an alternative is useful.
REPAIRABLE = {"UNSUPPORTED", "UNPRECEDENTED_IN_CORPUS", "RARE"}

# Relations describing configuration rather than data or control flow. A path
# made of flow edges cannot substitute for one of these: routing around a
# Cognito authorizer does not authorize anything.
CONFIGURATION_RELATIONS = {"configured_with"}


class InputError(ValueError):
    """Bad input from a caller, not a bug. Maps to a 400."""


def build_graph(
    input_type: str,
    content: str,
    vocab: Optional[Vocabulary] = None,
) -> ArchitectureGraph:
    if not content or not isinstance(content, str):
        raise InputError("content is required")
    if len(content.encode("utf-8")) > MAX_INPUT_BYTES:
        raise InputError("too_large")

    if input_type == "template":
        return parse_template(load_template(content), vocab or load_vocab()).graph
    if input_type == "mermaid":
        return parse_mermaid(content)
    if input_type == "prose":
        raise InputError(
            "Prose extraction runs in a separate function and is not wired yet. "
            "Send a template, a Mermaid diagram, or a graph."
        )
    raise InputError(f"unsupported input_type: {input_type!r}")


def repairable(verdict: EdgeVerdict, vocab: Vocabulary) -> bool:
    """Whether offering an alternative route makes sense for this edge.

    Three cases where it does not, each learned from a bad suggestion:

    * the label is fine already
    * the relation is configuration, not flow. Cognito fronting an API is not a
      pipe that can be re-plumbed
    * the source never emits anything. An auth or call-only service has no
      traffic to reroute, so any path out of it is an artefact of the graph
    """
    if verdict.label not in REPAIRABLE:
        return False
    if verdict.edge.relation in CONFIGURATION_RELATIONS:
        return False
    src = verdict.src_service
    if src in vocab.auth or src in vocab.call_only:
        return False
    return True


def attach_repairs(
    verdicts: list[EdgeVerdict],
    corpus_graph: nx.DiGraph,
    store: EdgeStore,
    vocab: Optional[Vocabulary] = None,
    *,
    max_hops: int = 3,
    max_paths: int = 2,
) -> None:
    """Add repair paths in place, for edges that need and can have one."""
    vocab = vocab or load_vocab()
    relays = vocab.with_role("relay")

    def facts(a: str, b: str):
        f = store.facts(a, b)
        return f.count, list(f.evidence)

    for v in verdicts:
        if not repairable(v, vocab):
            continue
        v.repairs = find_repairs(
            corpus_graph, v.src_service, v.dst_service, facts,
            max_hops=max_hops, max_paths=max_paths,
            is_relay=lambda s: s in relays,
        )


def corpus_graph_from_pairs(
    pairs: list[tuple[str, str, int]], rules: Optional[RuleTable] = None
) -> nx.DiGraph:
    rules = rules if rules is not None else load_rules()
    return build_corpus_graph(pairs, rules.is_known_unsupported)


def run_audit(
    graph: ArchitectureGraph,
    store: EdgeStore,
    corpus_graph: nx.DiGraph,
    *,
    corpus_commit: str = "unknown",
    vocab: Optional[Vocabulary] = None,
    rules: Optional[RuleTable] = None,
    pattern_index: Optional[PatternIndex] = None,
    timings: Optional[dict[str, int]] = None,
) -> AuditReport:
    vocab = vocab or load_vocab()
    rules = rules if rules is not None else load_rules()

    verdicts = ground_graph(graph, store, vocab, rules.unsupported_rule)
    attach_repairs(verdicts, corpus_graph, store, vocab)
    nodes = check_nodes(graph, vocab)
    closest = closest_patterns(graph, pattern_index) if pattern_index else []

    limitations = list(LIMITATIONS)
    pending = rules.stats()["pending_unsupported"]
    if pending:
        limitations.append(
            f"{pending} integration rules are written but not yet verified by a human, "
            f"so they are not affecting any verdict."
        )

    return AuditReport(
        audit_id=uuid.uuid4().hex[:16],
        corpus=CorpusInfo(commit=corpus_commit),
        graph=graph,
        edges=verdicts,
        nodes=nodes,
        closest_patterns=closest,
        summary=summarize(verdicts, nodes),
        limitations=limitations,
        timings_ms=timings or {},
    )
