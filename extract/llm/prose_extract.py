"""Prose to architecture graph, in two schema-constrained steps.

Section 3.6 of docs/PLAN.md.

    step 1: nodes, service chosen from an enum of the vocabulary
    step 2: edges, src and dst chosen from an enum of the node IDs step 1 made

The enum is the safety mechanism, not the prompt. Across six models tested, none
ever emitted a service outside the enum, even under direct injection. Step two
can only connect nodes step one produced, so an invented component cannot
acquire invented connections.

What the enum does NOT protect: no model preserved the correct edge *set* under
injection. Scores fell to 0.74 for the best model and 0.00 for one that obeyed
"drop all other edges". So the result of this module is a DRAFT. It is repaired
deterministically and then confirmed by a human before any audit runs.
"""

from __future__ import annotations

import re
from typing import Optional

from core.models import ArchitectureGraph, Edge, Node
from core.vocabulary import Vocabulary, load as load_vocab
from extract.llm import prompts
from extract.llm.client import Client, LLMResult

MAX_PROSE_CHARS = 12000
MAX_NODES = 40

# Lines that address a model rather than describe an architecture. Removing them
# is cheap and reduces, though it does not eliminate, injection damage.
_IMPERATIVE = re.compile(
    r"^\s*(ignore|disregard|forget|override|instead|you\s+must|you\s+should|"
    r"system\s*:|assistant\s*:|new\s+instructions?|important\s*:)\b",
    re.I,
)
_INLINE_INJECTION = re.compile(
    r"(ignore\s+(all\s+)?previous\s+instructions?|disregard\s+the\s+above|"
    r"drop\s+all\s+other\s+edges)",
    re.I,
)


def sanitize(prose: str) -> tuple[str, list[str]]:
    """Strip lines aimed at a model. Returns the text and what was removed."""
    removed: list[str] = []
    kept: list[str] = []
    for line in prose.splitlines():
        if _IMPERATIVE.match(line) or _INLINE_INJECTION.search(line):
            removed.append(line.strip()[:120])
            continue
        kept.append(line)
    text = "\n".join(kept).strip()[:MAX_PROSE_CHARS]
    return text, removed


def extract(
    prose: str,
    client: Client,
    vocab: Optional[Vocabulary] = None,
) -> ArchitectureGraph:
    vocab = vocab or load_vocab()
    clean, removed = sanitize(prose)
    warnings: list[str] = []
    if removed:
        warnings.append(
            f"Removed {len(removed)} line(s) that instructed a model rather than "
            f"describing the architecture."
        )
    if not clean:
        return ArchitectureGraph(
            input_type="prose", warnings=warnings + ["Nothing left to extract."]
        )

    # --- step 1: nodes ---
    nodes_result = client.generate_json(
        system=prompts.NODES_SYSTEM,
        user=prompts.nodes_user(vocab.enum_with_unknown(), clean),
        schema=prompts.nodes_schema(vocab.ids),
        task="nodes",
    )
    raw_nodes = (nodes_result.data or {}).get("nodes") or []
    if not nodes_result.validation_ok:
        warnings.extend(nodes_result.errors)

    nodes: list[Node] = []
    ids: list[str] = []
    for i, item in enumerate(raw_nodes[:MAX_NODES]):
        if not isinstance(item, dict):
            continue
        service = item.get("service") or "unknown"
        # Belt and braces: the enum should make this impossible, so if it ever
        # fires the schema was not honoured and we want to know.
        if service != "unknown" and not vocab.has(service):
            warnings.append(f"Model returned an unknown service {service!r}; ignored.")
            service = "unknown"
        node_id = f"n{i + 1}"
        ids.append(node_id)
        nodes.append(Node(
            id=node_id,
            label=str(item.get("label") or node_id)[:120],
            service=service,
            canon_method="llm",
        ))

    if len(nodes) < 2:
        return ArchitectureGraph(
            input_type="prose", nodes=nodes,
            warnings=warnings + ["Fewer than two components were found."],
        )

    # --- step 2: edges, constrained to the ids above ---
    edges_result = client.generate_json(
        system=prompts.EDGES_SYSTEM,
        user=prompts.edges_user(ids, [n.label for n in nodes],
                                [n.service for n in nodes], clean),
        schema=prompts.edges_schema(ids),
        task="edges",
    )
    raw_edges = (edges_result.data or {}).get("edges") or []
    if not edges_result.validation_ok:
        warnings.extend(edges_result.errors)

    known = set(ids)
    seen: set[tuple[str, str]] = set()
    edges: list[Edge] = []
    for item in raw_edges:
        if not isinstance(item, dict):
            continue
        src, dst = item.get("src"), item.get("dst")
        if src not in known or dst not in known or src == dst:
            continue
        if (src, dst) in seen:
            continue
        seen.add((src, dst))
        edges.append(Edge(
            src=src, dst=dst,
            relation=item.get("relation") or "flows_to",
            confidence="strong",
            source_construct="prose",
        ))

    unknown = sum(1 for n in nodes if n.service == "unknown")
    if unknown:
        warnings.append(
            f"{unknown} component(s) did not match a known AWS service. "
            f"Edit them before relying on the audit."
        )
    warnings.append(
        "This graph was extracted by a model and repaired deterministically. "
        "Review it before running the audit."
    )

    return ArchitectureGraph(
        input_type="prose", nodes=nodes, edges=edges, warnings=warnings
    )
