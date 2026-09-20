"""Deterministic repair of an extracted architecture graph.

Sections 3.7 and 3.8 of docs/PLAN.md.

Every model tested during the pilot made the same three mistakes on every run,
regardless of size or vendor. Systematic errors belong in code, not in prompts:
elaborating the prompt moved the best local model not at all and made the small
one worse. Measured effect of this module on the pilot designs was +0.11 mean
edge F1 for qwen2.5:7b and +0.12 for llama3.2:3b.

No model call happens here. This runs on the output of extract/llm/ and its
result is shown to the user for confirmation before any audit.
"""

from __future__ import annotations

from typing import Callable, Optional

from core.models import ArchitectureGraph, Edge
from core.vocabulary import Vocabulary, load as load_vocab

# Given (src_service, dst_service), return how many corpus patterns contain it.
CountFn = Callable[[str, str], int]


def postprocess(
    graph: ArchitectureGraph,
    vocab: Optional[Vocabulary] = None,
    count_fn: Optional[CountFn] = None,
    *,
    flip_min_reverse: int = 3,
) -> ArchitectureGraph:
    """Return a repaired copy of `graph`, recording every change in warnings.

    `count_fn` enables corpus-guided direction repair (section 3.8). Omit it and
    that pass is skipped, which is what the parser paths do: template and Mermaid
    input state direction explicitly and must not be second-guessed.
    """
    vocab = vocab or load_vocab()
    g = graph.model_copy(deep=True)

    edges = _drop_self_loops(g, g.edges)
    edges = _rule_a_call_only(g, edges, vocab)
    edges = _rule_b_auth_attachment(g, edges, vocab)
    edges = _rule_c_pull_direction(g, edges, vocab)
    if count_fn is not None:
        edges = _rule_d_corpus_direction(g, edges, count_fn, flip_min_reverse)
    edges = _dedupe(g, edges)

    g.edges = edges
    return g


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _svc(g: ArchitectureGraph, node_id: str) -> str:
    return g.service_of(node_id)


def _label(g: ArchitectureGraph, node_id: str) -> str:
    n = g.node_by_id(node_id)
    return n.label if n else node_id


def _warn(g: ArchitectureGraph, msg: str) -> None:
    if msg not in g.warnings:
        g.warnings.append(msg)


def _drop_self_loops(g: ArchitectureGraph, edges: list[Edge]) -> list[Edge]:
    kept = []
    for e in edges:
        if e.src == e.dst:
            _warn(g, f"Removed a self-loop on {_label(g, e.src)}.")
            continue
        kept.append(e)
    return kept


def _dedupe(g: ArchitectureGraph, edges: list[Edge]) -> list[Edge]:
    seen: set[tuple[str, str]] = set()
    kept: list[Edge] = []
    for e in edges:
        key = e.pair()
        if key in seen:
            continue
        seen.add(key)
        kept.append(e)
    return kept


# --------------------------------------------------------------------------
# Rule A: actor confusion
# --------------------------------------------------------------------------


def _rule_a_call_only(
    g: ArchitectureGraph, edges: list[Edge], vocab: Vocabulary
) -> list[Edge]:
    """A call-only service does not originate edges.

    Models consistently credit the called service with the caller's downstream
    write, producing textract -> dynamodb while the extractor Lambda's write
    goes missing. Drop the edge and reattach it to whoever called that service.
    """
    callers: dict[str, list[str]] = {}
    for e in edges:
        callers.setdefault(e.dst, []).append(e.src)

    kept: list[Edge] = []
    for e in edges:
        src_service = _svc(g, e.src)
        if src_service not in vocab.call_only:
            kept.append(e)
            continue

        dst_service = _svc(g, e.dst)
        if dst_service in vocab.call_only:
            # call-only -> call-only: no sensible reattachment, drop it.
            _warn(
                g,
                f"Dropped {_label(g, e.src)} -> {_label(g, e.dst)}: "
                f"{vocab.title(src_service)} is only ever called, it does not emit.",
            )
            continue

        candidates = [
            c for c in callers.get(e.src, []) if _svc(g, c) not in vocab.call_only
        ]
        if not candidates:
            _warn(
                g,
                f"Dropped {_label(g, e.src)} -> {_label(g, e.dst)}: "
                f"{vocab.title(src_service)} does not originate connections.",
            )
            continue

        caller = candidates[0]
        kept.append(
            Edge(
                src=caller,
                dst=e.dst,
                relation=e.relation,
                confidence=e.confidence,
                source_construct=e.source_construct,
            )
        )
        _warn(
            g,
            f"Reattached the connection into {_label(g, e.dst)}: the caller "
            f"{_label(g, caller)} performs it, not {_label(g, e.src)}.",
        )
    return kept


# --------------------------------------------------------------------------
# Rule B: auth attachment
# --------------------------------------------------------------------------


def _rule_b_auth_attachment(
    g: ArchitectureGraph, edges: list[Edge], vocab: Vocabulary
) -> list[Edge]:
    """An identity service connects INTO the api_front node it protects.

    Known bug this avoids: an early draft attached Cognito to whichever
    api_front node appeared first in the list, wiring it to CloudFront in a
    design where CloudFront was listed before API Gateway. Choose by adjacency
    in the extracted graph, never by list order.
    """
    auth_nodes = {n.id for n in g.nodes if _svc(g, n.id) in vocab.auth}
    if not auth_nodes:
        return edges

    api_nodes = [n.id for n in g.nodes if _svc(g, n.id) in vocab.api_front]
    if not api_nodes:
        return edges

    # adjacency ignoring direction, so "apigateway -> cognito" still counts
    neighbours: dict[str, set[str]] = {}
    for e in edges:
        neighbours.setdefault(e.src, set()).add(e.dst)
        neighbours.setdefault(e.dst, set()).add(e.src)

    kept: list[Edge] = []
    rewired: set[tuple[str, str]] = set()

    for e in edges:
        src_is_auth = e.src in auth_nodes
        dst_is_auth = e.dst in auth_nodes

        if not src_is_auth and not dst_is_auth:
            kept.append(e)
            continue

        auth_node = e.src if src_is_auth else e.dst
        other = e.dst if src_is_auth else e.src

        # already correct: auth -> api_front
        if src_is_auth and _svc(g, other) in vocab.api_front:
            kept.append(e)
            continue

        target = _pick_api_front(auth_node, other, api_nodes, neighbours)
        if target is None:
            _warn(
                g,
                f"Dropped a connection on {_label(g, auth_node)}: no API surface "
                f"for it to protect.",
            )
            continue

        key = (auth_node, target)
        if key in rewired:
            continue
        rewired.add(key)

        kept.append(Edge(src=auth_node, dst=target, relation="configured_with"))
        _warn(
            g,
            f"Redirected {_label(g, auth_node)} to protect {_label(g, target)}. "
            f"An identity service secures an API surface; traffic does not flow out of it.",
        )
    return kept


def _pick_api_front(
    auth_node: str,
    other: str,
    api_nodes: list[str],
    neighbours: dict[str, set[str]],
) -> Optional[str]:
    # 1. the node it was already wired to, if that is an API surface
    if other in api_nodes:
        return other
    # 2. an API surface adjacent to the auth node
    for cand in api_nodes:
        if cand in neighbours.get(auth_node, set()):
            return cand
    # 3. an API surface adjacent to whatever it was wired to
    for cand in api_nodes:
        if cand in neighbours.get(other, set()):
            return cand
    # 4. the only API surface in the design
    if len(api_nodes) == 1:
        return api_nodes[0]
    return None


# --------------------------------------------------------------------------
# Rule C: pull direction
# --------------------------------------------------------------------------


def _rule_c_pull_direction(
    g: ArchitectureGraph, edges: list[Edge], vocab: Vocabulary
) -> list[Edge]:
    """For a pull-based source, the source is the queue.

    "an ingest Lambda polls that queue" is routinely extracted as
    lambda -> sqs. The corpus convention, and the event-source-mapping reality,
    is sqs -> lambda.
    """
    kept: list[Edge] = []
    for e in edges:
        src_service = _svc(g, e.src)
        dst_service = _svc(g, e.dst)

        consuming = (
            dst_service in vocab.pull_sources
            and src_service not in vocab.pull_sources
            and vocab.has_role(src_service, "compute")
            and e.relation in vocab.consume_relations
        )
        if consuming:
            kept.append(
                Edge(
                    src=e.dst,
                    dst=e.src,
                    relation="triggers",
                    confidence=e.confidence,
                    source_construct=e.source_construct,
                )
            )
            _warn(
                g,
                f"Reversed {_label(g, e.src)} -> {_label(g, e.dst)}. "
                f"{vocab.title(dst_service)} is the event source; the consumer is "
                f"triggered by it.",
            )
            continue
        kept.append(e)
    return kept


# --------------------------------------------------------------------------
# Rule D: corpus-guided direction repair (section 3.8)
# --------------------------------------------------------------------------


def _rule_d_corpus_direction(
    g: ArchitectureGraph,
    edges: list[Edge],
    count_fn: CountFn,
    flip_min_reverse: int,
) -> list[Edge]:
    """Flip an edge with no precedent whose reverse is well precedented.

    Costs nothing: the indexer already produces count(src, dst) for grounding.
    """
    kept: list[Edge] = []
    for e in edges:
        a, b = _svc(g, e.src), _svc(g, e.dst)
        if a == "unknown" or b == "unknown":
            kept.append(e)
            continue
        forward = count_fn(a, b)
        reverse = count_fn(b, a)
        if forward == 0 and reverse >= flip_min_reverse:
            kept.append(
                Edge(
                    src=e.dst,
                    dst=e.src,
                    relation=e.relation,
                    confidence=e.confidence,
                    source_construct=e.source_construct,
                )
            )
            _warn(
                g,
                f"Reversed {_label(g, e.src)} -> {_label(g, e.dst)}: no pattern in the "
                f"corpus connects them that way, while {reverse} connect them the other way.",
            )
            continue
        kept.append(e)
    return kept
