"""Repair: find a well-precedented path where a direct edge will not work.

Section 4 of docs/PLAN.md.

    Corpus service graph: nodes = services, edges = src->dst with count >= 1
      and not known to be unsupported.
    weight(e) = 1 + 1 / count      # every hop costs at least 1; common hops cost less
    Return up to 2 simple paths, at most 3 hops, ordered by total weight.

Two constraints make a suggestion trustworthy rather than merely graph-valid.

**Weighting.** A plain shortest path would happily route through a connection
that appears in exactly one pattern. Adding 1/count makes a well-trodden hop
cheaper than a rare one, so the suggestion is conventional, not just possible.

**Relays.** Every intermediate node must be able to receive and then emit.
Without this the engine suggested `cognito -> lambda -> apigateway` for a Cognito
authorizer edge: both hops were individually well precedented, and the path was
nonsense, because Cognito authorizes rather than carrying traffic onward. A hop
being frequent in the corpus does not make it composable.

If no path exists we say so. Inventing a route we cannot evidence would be the
same failure the product exists to catch.
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional

import networkx as nx

from core.models import Evidence, RepairPath

MAX_HOPS = 3
MAX_PATHS = 2
# shortest_simple_paths is lazy but unbounded; stop enumerating once it is clear
# no acceptable path is coming.
MAX_CANDIDATES = 60

# (src, dst) -> (count, evidence)
FactsFn = Callable[[str, str], tuple[int, list[Evidence]]]
UnsupportedFn = Callable[[str, str], bool]
RelayFn = Callable[[str], bool]


def build_corpus_graph(
    pairs: Iterable[tuple[str, str, int]],
    is_unsupported: Optional[UnsupportedFn] = None,
) -> nx.DiGraph:
    """Directed graph of every service pair the corpus has seen at least once.

    Edges we know to be unsupported are left out entirely: routing a repair
    through an impossible hop would be worse than finding no path.
    """
    g = nx.DiGraph()
    for src, dst, count in pairs:
        if count < 1 or src == dst:
            continue
        if is_unsupported is not None and is_unsupported(src, dst):
            continue
        g.add_edge(src, dst, count=count, weight=1.0 + 1.0 / count)
    return g


def find_repairs(
    graph: nx.DiGraph,
    src_service: str,
    dst_service: str,
    facts: Optional[FactsFn] = None,
    *,
    max_hops: int = MAX_HOPS,
    max_paths: int = MAX_PATHS,
    is_relay: Optional[RelayFn] = None,
) -> list[RepairPath]:
    """Up to `max_paths` simple paths from src to dst, cheapest first.

    `is_relay` decides which services may appear in the MIDDLE of a path. The
    endpoints are exempt: they are the edge the user actually drew.
    """
    if src_service not in graph or dst_service not in graph:
        return []
    if src_service == dst_service:
        return []

    found: list[RepairPath] = []
    seen = 0
    try:
        candidates = nx.shortest_simple_paths(
            graph, src_service, dst_service, weight="weight"
        )
        for services in candidates:
            seen += 1
            if seen > MAX_CANDIDATES:
                break
            hops = len(services) - 1
            # Paths come out cheapest first, not shortest first: four common
            # hops can cost less than three rare ones. So an over-long path is
            # skipped, not a signal to stop; MAX_CANDIDATES bounds the search.
            if hops > max_hops:
                continue
            # The direct edge is what is being repaired. For a RARE edge it is
            # in the corpus graph, and offering it back as its own alternative
            # would be circular.
            if hops < 2:
                continue
            if is_relay is not None and not all(is_relay(x) for x in services[1:-1]):
                continue  # an intermediate that cannot forward is not a route
            found.append(_to_repair_path(graph, services, facts))
            if len(found) >= max_paths:
                break
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []
    return found


def _to_repair_path(
    graph: nx.DiGraph, services: list[str], facts: Optional[FactsFn]
) -> RepairPath:
    hop_counts: list[int] = []
    hop_evidence: list[list[Evidence]] = []
    total = 0.0
    for a, b in zip(services, services[1:]):
        data = graph.get_edge_data(a, b) or {}
        count = int(data.get("count", 0))
        total += float(data.get("weight", 1.0))
        hop_counts.append(count)
        if facts is not None:
            _, evidence = facts(a, b)
            hop_evidence.append(list(evidence)[:3])
        else:
            hop_evidence.append([])
    return RepairPath(
        services=list(services),
        hop_counts=hop_counts,
        hop_evidence=hop_evidence,
        total_weight=round(total, 4),
    )


def explain(path: RepairPath) -> str:
    """One plain sentence a judge can read off the screen."""
    if not path.services:
        return "No grounded path was found in this corpus."
    arrow = " then ".join(path.services)
    weakest = min(path.hop_counts) if path.hop_counts else 0
    return (
        f"Route it as {arrow}. "
        f"{'Every hop' if weakest >= 3 else 'The least common hop'} appears in "
        f"{weakest} corpus pattern{'s' if weakest != 1 else ''}."
    )


NO_PATH_MESSAGE = (
    "No grounded path exists in this corpus. That does not mean none exists in AWS, "
    "only that this corpus does not show one."
)
