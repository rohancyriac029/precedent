"""Mermaid flowchart parser.

The demo's primary input: an LLM asked for an architecture usually answers with
a Mermaid diagram, and that is exactly the artefact this product audits.

Supports `flowchart` and `graph` with the shapes people actually use:

    A[Label] --> B(Label)
    A -->|publishes| B
    A --> B --> C            chained
    subgraph X ... end       flattened, since a subgraph is not a service

Unsupported syntax produces a warning, never an exception. A diagram that
defeats one line must still yield every edge on the other lines.
"""

from __future__ import annotations

import re
from typing import Optional

from core.canonicalize import Canonicalizer, load as load_canon
from core.models import ArchitectureGraph, Edge, Node, Relation

# Node shapes: [box] (round) ([stadium]) [[subroutine]] [(cylinder)] {rhombus}
# {{hexagon}} >flag] ((circle))
_SHAPES = [
    (r"\(\[", r"\]\)"), (r"\[\[", r"\]\]"), (r"\[\(", r"\)\]"),
    (r"\{\{", r"\}\}"), (r"\(\(", r"\)\)"),
    (r"\[", r"\]"), (r"\(", r"\)"), (r"\{", r"\}"), (r">", r"\]"),
]
_NODE_DEF = re.compile(
    r"(?P<id>[A-Za-z0-9_.-]+)\s*(?:"
    + "|".join(f"(?:{o}(?P<l{i}>[^\\]\\)\\}}]*?){c})" for i, (o, c) in enumerate(_SHAPES))
    + r")?"
)

# --> --- -.-> ==> --o --x, optionally |label| or -- label -->
_LINK = re.compile(
    r"\s*(?P<arrow>-{2,3}>|-{3,}|-\.->|-\.-|={2,}>|--[ox]|<-{2,3})"
    r"\s*(?:\|(?P<label1>[^|]*)\|)?\s*"
)
_EDGE_LABEL_INLINE = re.compile(r"--\s*(?P<label>[^-|>]+?)\s*-{2,3}>")

_HEADER = re.compile(r"^\s*(flowchart|graph)\s+(TB|TD|BT|RL|LR)?\s*$", re.I)
_SUBGRAPH = re.compile(r"^\s*subgraph\b", re.I)
_END = re.compile(r"^\s*end\s*$", re.I)
_DIRECTIVE = re.compile(r"^\s*(classDef|class|style|linkStyle|click|%%|direction)\b", re.I)

RELATION_WORDS: dict[str, Relation] = {
    "trigger": "triggers", "triggers": "triggers", "invoke": "invokes",
    "invokes": "invokes", "calls": "invokes", "call": "invokes",
    "read": "reads", "reads": "reads", "query": "reads", "queries": "reads",
    "get": "reads", "gets": "reads", "fetch": "reads",
    "write": "writes", "writes": "writes", "put": "writes", "puts": "writes",
    "store": "writes", "stores": "writes", "save": "writes", "saves": "writes",
    "publish": "publishes", "publishes": "publishes",
    "send": "sends", "sends": "sends", "sends to": "sends",
    "subscribe": "subscribes", "subscribes": "subscribes",
    "start": "starts_execution", "starts": "starts_execution",
    "execute": "starts_execution",
}


def _relation_from_label(label: Optional[str]) -> Relation:
    if not label:
        return "flows_to"
    text = label.strip().lower()
    if text in RELATION_WORDS:
        return RELATION_WORDS[text]
    for word, relation in RELATION_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", text):
            return relation
    return "flows_to"


class MermaidParser:
    def __init__(self, text: str, canon: Optional[Canonicalizer] = None):
        self.text = text
        self.canon = canon or load_canon()
        self.labels: dict[str, str] = {}
        self.edges: list[tuple[str, str, Relation]] = []
        self.warnings: list[str] = []
        self._subgraph_ids: set[str] = set()

    def parse(self) -> ArchitectureGraph:
        lines = self.text.splitlines()
        if not any(_HEADER.match(ln) for ln in lines):
            self.warnings.append(
                "No 'flowchart' or 'graph' header found. Parsed the lines anyway."
            )

        for lineno, raw in enumerate(lines, 1):
            line = raw.split("%%", 1)[0].rstrip()
            if not line.strip():
                continue
            if _HEADER.match(line) or _END.match(line) or _DIRECTIVE.match(line):
                continue
            if _SUBGRAPH.match(line):
                # A subgraph is a visual grouping, not a service. Flatten it.
                m = _NODE_DEF.search(line[len("subgraph"):].strip())
                if m:
                    self._subgraph_ids.add(m.group("id"))
                continue
            try:
                self._line(line)
            except Exception as exc:  # noqa: BLE001 - one bad line must not stop the rest
                self.warnings.append(f"line {lineno}: could not parse ({type(exc).__name__})")

        return self._build()

    # -- line handling ------------------------------------------------------

    def _line(self, line: str) -> None:
        if not _LINK.search(line):
            self._record_node(line.strip())
            return

        segments, arrows = self._split_chain(line)
        ids = [self._record_node(s) for s in segments]
        for i, (a, b) in enumerate(zip(ids, ids[1:])):
            if not a or not b:
                continue
            arrow, label = arrows[i]
            relation = _relation_from_label(label)
            if arrow.startswith("<"):
                self.edges.append((b, a, relation))
            else:
                self.edges.append((a, b, relation))

    def _split_chain(self, line: str) -> tuple[list[str], list[tuple[str, Optional[str]]]]:
        """Split `A --> B -->|x| C` into segments and the arrows between them."""
        segments: list[str] = []
        arrows: list[tuple[str, Optional[str]]] = []
        pos = 0
        while True:
            inline = _EDGE_LABEL_INLINE.search(line, pos)
            m = _LINK.search(line, pos)
            if inline and (not m or inline.start() <= m.start()):
                segments.append(line[pos:inline.start()].strip())
                arrows.append(("-->", inline.group("label")))
                pos = inline.end()
                continue
            if not m:
                break
            segments.append(line[pos:m.start()].strip())
            arrows.append((m.group("arrow"), m.group("label1")))
            pos = m.end()
        segments.append(line[pos:].strip())
        return segments, arrows

    def _record_node(self, segment: str) -> Optional[str]:
        segment = segment.strip().rstrip(";")
        if not segment:
            return None
        m = _NODE_DEF.match(segment)
        if not m:
            return None
        node_id = m.group("id")
        label = next(
            (m.group(g) for g in m.groupdict()
             if g.startswith("l") and m.group(g) not in (None, "")),
            None,
        )
        if label:
            self.labels[node_id] = _clean_label(label)
        else:
            self.labels.setdefault(node_id, node_id)
        return node_id

    # -- assembly -----------------------------------------------------------

    def _build(self) -> ArchitectureGraph:
        nodes: list[Node] = []
        unknown: list[str] = []
        for node_id, label in self.labels.items():
            if node_id in self._subgraph_ids:
                continue
            service, method, score = self.canon.resolve(label)
            if service == "unknown" and label != node_id:
                # try the identifier too: "S3Bucket --> Fn" carries meaning
                service, method, score = self.canon.resolve(node_id)
            if service == "unknown":
                unknown.append(label)
            nodes.append(Node(
                id=node_id, label=label, service=service,
                canon_method=method, canon_score=score, source_ref=node_id,
            ))

        known = {n.id for n in nodes}
        edges = [
            Edge(src=a, dst=b, relation=r, confidence="strong", source_construct="mermaid")
            for a, b, r in self.edges
            if a in known and b in known and a != b
        ]

        seen: set[tuple[str, str]] = set()
        deduped: list[Edge] = []
        for e in edges:
            if e.pair() in seen:
                continue
            seen.add(e.pair())
            deduped.append(e)

        if unknown:
            shown = ", ".join(sorted(set(unknown))[:5])
            self.warnings.append(
                f"{len(set(unknown))} component(s) did not match a known AWS service "
                f"and were left unmapped: {shown}. Edit them before relying on the audit."
            )

        return ArchitectureGraph(
            input_type="mermaid", nodes=nodes, edges=deduped, warnings=self.warnings
        )


def _clean_label(label: str) -> str:
    text = label.strip().strip('"').strip("'")
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"[#&][a-z0-9]+;", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def parse_mermaid(text: str, canon: Optional[Canonicalizer] = None) -> ArchitectureGraph:
    return MermaidParser(text, canon).parse()
