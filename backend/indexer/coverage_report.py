"""Run the parser over the whole corpus and report how much of it we understand.

Gate 2 wants two numbers: no unhandled exception across the SAM/CFN subset, and
at least 80% of those templates producing at least one edge.

This also surfaces *why* templates produce nothing, which is the actionable part.
A template with no edges is usually one of three things: a pattern that genuinely
has no service-to-service connection, a construct we have not implemented, or a
reference we refused to guess at.
"""

from __future__ import annotations

import json
import re
import traceback
from collections import Counter
from pathlib import Path

from core.cfn_loader import TemplateParseError, load_file
from core.template_parser import parse_template
from core.vocabulary import load as load_vocab
from indexer.census import CORPUS, pattern_dirs, sam_cfn_templates

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "eval" / "results" / "coverage.json"


def main() -> None:
    vocab = load_vocab()
    templates = 0
    parsed = 0
    with_edges = 0
    crashed: list[dict] = []
    unparseable: list[str] = []
    empty: list[str] = []
    missed: list[str] = []
    connectable = 0
    edge_counter: Counter = Counter()
    relation_counter: Counter = Counter()
    construct_counter: Counter = Counter()
    warning_kinds: Counter = Counter()
    total_edges = 0
    total_weak = 0

    for pattern in pattern_dirs(CORPUS):
        for tpl in sam_cfn_templates(pattern):
            templates += 1
            rel = str(tpl.relative_to(CORPUS))
            try:
                loaded = load_file(tpl)
            except TemplateParseError:
                unparseable.append(rel)
                continue
            except Exception:
                crashed.append({"template": rel, "stage": "load",
                                "error": traceback.format_exc(limit=3)})
                continue

            try:
                result = parse_template(loaded, vocab)
            except Exception:
                crashed.append({"template": rel, "stage": "parse",
                                "error": traceback.format_exc(limit=3)})
                continue

            parsed += 1
            g = result.graph
            strong = [e for e in g.edges if e.confidence == "strong"]
            total_edges += len(strong)
            total_weak += len(g.edges) - len(strong)

            for e in strong:
                a, b = g.service_of(e.src), g.service_of(e.dst)
                if a != b:
                    edge_counter[(a, b)] += 1
                relation_counter[e.relation] += 1
                if e.source_construct:
                    construct_counter[e.source_construct] += 1

            # A template holding fewer than two connectable resources cannot
            # produce an edge no matter how good the parser is. Counting those
            # as parser misses understates coverage and hides the real gaps.
            node_count = sum(
                1 for _lid, rt, _p in loaded.iter_resources() if vocab.is_node_type(rt)
            )
            if node_count >= 2:
                connectable += 1

            if any(g.service_of(e.src) != g.service_of(e.dst) for e in strong):
                with_edges += 1
            else:
                empty.append(rel)
                if node_count >= 2:
                    missed.append(rel)

            for w in result.warnings:
                warning_kinds[_warning_kind(w)] += 1

    coverage = with_edges / templates if templates else 0.0
    connectable_coverage = with_edges / connectable if connectable else 0.0
    data = {
        "templates_total": templates,
        "templates_parsed": parsed,
        "templates_unparseable": len(unparseable),
        "templates_crashed": len(crashed),
        "templates_with_edges": with_edges,
        "parse_coverage": round(coverage, 4),
        "templates_connectable": connectable,
        "parse_coverage_connectable": round(connectable_coverage, 4),
        "missed_connectable_sample": missed[:40],
        "missed_connectable_count": len(missed),
        "strong_edges_total": total_edges,
        "weak_edges_total": total_weak,
        "distinct_service_edges": len(edge_counter),
        "top_service_edges": [
            {"src": a, "dst": b, "patterns": n}
            for (a, b), n in edge_counter.most_common(40)
        ],
        "relations": dict(relation_counter.most_common()),
        "source_constructs": dict(construct_counter.most_common()),
        "warning_kinds": dict(warning_kinds.most_common()),
        "templates_without_edges_sample": empty[:30],
        "crashes": crashed[:10],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")

    print(f"templates                : {templates}")
    print(f"  parsed without crashing: {parsed}")
    print(f"  unparseable YAML/JSON  : {len(unparseable)}")
    print(f"  CRASHED                : {len(crashed)}   <- Gate 2 wants 0")
    print(f"  produced >= 1 edge     : {with_edges}")
    print()
    print(f"connectable templates    : {connectable}  (>= 2 resources that can be joined)")
    print(f"parse coverage, all      : {coverage:.1%}")
    print(f"parse coverage, connectable: {connectable_coverage:.1%}   <- Gate 2 wants >= 80%")
    print(f"genuine parser misses    : {len(missed)}")
    print(f"strong service edges     : {total_edges}")
    print(f"weak edges (excluded)    : {total_weak}")
    print(f"distinct service pairs   : {len(edge_counter)}")
    print()
    print("top 20 service edges by pattern count:")
    for (a, b), n in edge_counter.most_common(20):
        print(f"  {a:22s} -> {b:22s} {n}")
    print()
    print("edges by source construct:")
    for c, n in construct_counter.most_common(12):
        print(f"  {c:46s} {n}")
    if crashed:
        print("\nFIRST CRASH:")
        print(crashed[0]["template"])
        print(crashed[0]["error"])
    print(f"\nwritten to {OUT.relative_to(ROOT)}")


def _warning_kind(w: str) -> str:
    """Collapse a warning to its category so the counts are useful."""
    if "literal ARN" in w:
        return "literal_arn"
    if "not a resource in this template" in w:
        return "external_or_parameter_ref"
    if "ambiguous" in w:
        return "ambiguous_ref"
    if "rule failed" in w:
        return "rule_exception"
    if "no Type" in w or "not a mapping" in w:
        return "malformed_resource"
    if "no Resources" in w:
        return "no_resources_section"
    return re.sub(r"[^a-z_]", "", w.lower().replace(" ", "_"))[:40] or "other"


if __name__ == "__main__":
    main()
