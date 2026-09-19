"""End-to-end tests for the deterministic pipeline.

These run the real code path the deployed Lambda runs, with an in-memory edge
store, so the whole product can be verified without deploying anything.
"""

from __future__ import annotations

import pytest

from core.audit import InputError, build_graph, corpus_graph_from_pairs, run_audit
from core.grounding import DictEdgeStore
from core.models import Evidence
from core.rules import Rule, RuleTable

# A corpus shaped like the real one, small enough to reason about.
PAIRS = [
    ("s3", "lambda", 18), ("s3", "eventbridge", 12), ("s3", "sqs", 4),
    ("eventbridge", "step_functions", 12), ("eventbridge", "lambda", 28),
    ("eventbridge", "sqs", 8), ("sqs", "lambda", 31), ("sns", "sqs", 10),
    ("lambda", "dynamodb", 43), ("lambda", "sns", 9), ("lambda", "s3", 22),
    ("step_functions", "lambda", 13), ("apigateway", "lambda", 101),
    ("lambda", "step_functions", 4),
]

COUNTS = {(a, b): c for a, b, c in PAIRS}
EVIDENCE = {
    (a, b): [Evidence(pattern_id=f"{a}-{b}", title=f"{a} to {b}", url="https://example/x")]
    for a, b, _ in PAIRS
}

VERIFIED_RULES = RuleTable([
    Rule({"rule_id": "R005", "src_service": "s3", "dst_service": "step_functions",
          "direct_supported": "false",
          "doc_url": "https://docs.aws.amazon.com/s3-notifications",
          "verified_by": "rc", "note": "S3 cannot start a state machine directly."}),
    Rule({"rule_id": "R004", "src_service": "s3", "dst_service": "eventbridge",
          "direct_supported": "true", "doc_url": "https://docs.aws.amazon.com/s3-eb",
          "verified_by": "rc"}),
])

DEMO = """
flowchart LR
  S3[Document Bucket] --> SFN[Processing State Machine]
  SFN --> L[Extract Lambda]
  L --> DDB[(Results Table)]
  L --> SNS[Alert Topic]
  L --> EB[Event Bus]
  SNS --> Q[Notify Queue]
  EB --> Q
"""


@pytest.fixture
def store():
    return DictEdgeStore(COUNTS, EVIDENCE)


@pytest.fixture
def cgraph():
    return corpus_graph_from_pairs(PAIRS, VERIFIED_RULES)


def _verdict(report, src, dst):
    return next(v for v in report.edges if v.src_service == src and v.dst_service == dst)


def test_demo_diagram_flags_the_unsupported_edge(store, cgraph):
    graph = build_graph("mermaid", DEMO)
    report = run_audit(graph, store, cgraph, rules=VERIFIED_RULES, corpus_commit="abc123")
    v = _verdict(report, "s3", "step_functions")
    assert v.label == "UNSUPPORTED"
    assert v.rule is not None
    assert v.rule.doc_url.startswith("https://")


def test_the_unsupported_edge_gets_a_grounded_repair(store, cgraph):
    graph = build_graph("mermaid", DEMO)
    report = run_audit(graph, store, cgraph, rules=VERIFIED_RULES)
    v = _verdict(report, "s3", "step_functions")
    assert v.repairs, "the demo edge must offer a repair"
    assert v.repairs[0].services == ["s3", "eventbridge", "step_functions"]
    assert all(c >= 3 for c in v.repairs[0].hop_counts)


def test_repair_hops_carry_evidence(store, cgraph):
    graph = build_graph("mermaid", DEMO)
    report = run_audit(graph, store, cgraph, rules=VERIFIED_RULES)
    path = _verdict(report, "s3", "step_functions").repairs[0]
    assert len(path.hop_evidence) == path.hops
    assert path.hop_evidence[0][0].url.startswith("https://")


def test_repair_never_routes_through_the_unsupported_edge(store, cgraph):
    graph = build_graph("mermaid", DEMO)
    report = run_audit(graph, store, cgraph, rules=VERIFIED_RULES)
    for path in _verdict(report, "s3", "step_functions").repairs:
        hops = list(zip(path.services, path.services[1:]))
        assert ("s3", "step_functions") not in hops


def test_well_precedented_edges_are_grounded(store, cgraph):
    graph = build_graph("mermaid", DEMO)
    report = run_audit(graph, store, cgraph, rules=VERIFIED_RULES)
    assert _verdict(report, "lambda", "dynamodb").label == "GROUNDED"
    assert _verdict(report, "sns", "sqs").label == "GROUNDED"


def test_overlapping_capability_is_flagged_as_a_question(store, cgraph):
    """SNS and EventBridge share a producer and a consumer in the demo."""
    graph = build_graph("mermaid", DEMO)
    report = run_audit(graph, store, cgraph, rules=VERIFIED_RULES)
    overlaps = [n for n in report.nodes if "OVERLAPPING_CAPABILITY" in n.flags]
    assert overlaps
    assert "Do you need both?" in overlaps[0].note


def test_summary_mentions_the_unsupported_edge(store, cgraph):
    graph = build_graph("mermaid", DEMO)
    report = run_audit(graph, store, cgraph, rules=VERIFIED_RULES)
    assert "not supported" in report.summary
    assert "novel" not in report.summary.lower()
    assert "original" not in report.summary.lower()


def test_unverified_rules_do_not_produce_unsupported_verdicts(store, cgraph):
    """The shipped table has no initials, so nothing may be marked UNSUPPORTED."""
    from core.rules import load_rules
    shipped = load_rules()
    graph = build_graph("mermaid", DEMO)
    report = run_audit(graph, store, cgraph, rules=shipped)
    assert all(v.label != "UNSUPPORTED" for v in report.edges)
    assert any("not yet verified" in l for l in report.limitations)


def test_report_is_deterministic(store, cgraph):
    graph = build_graph("mermaid", DEMO)
    a = run_audit(graph, store, cgraph, rules=VERIFIED_RULES)
    b = run_audit(graph, store, cgraph, rules=VERIFIED_RULES)
    assert a.audit_id != b.audit_id
    assert a.deterministic_fingerprint() == b.deterministic_fingerprint()


def test_limitations_never_claim_impossibility(store, cgraph):
    graph = build_graph("mermaid", DEMO)
    report = run_audit(graph, store, cgraph, rules=VERIFIED_RULES)
    assert any("not evidence that a connection is impossible" in l
               for l in report.limitations)


def test_template_input_works_through_the_same_path(store, cgraph):
    graph = build_graph("template", """
Resources:
  Bucket:
    Type: AWS::S3::Bucket
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Events:
        Up:
          Type: S3
          Properties:
            Bucket: !Ref Bucket
""")
    report = run_audit(graph, store, cgraph, rules=VERIFIED_RULES)
    assert _verdict(report, "s3", "lambda").label == "GROUNDED"


def test_bad_input_type_raises_input_error():
    with pytest.raises(InputError):
        build_graph("powerpoint", "x")


def test_prose_is_refused_clearly_rather_than_half_working():
    with pytest.raises(InputError) as exc:
        build_graph("prose", "a lambda reads from s3")
    assert "not wired yet" in str(exc.value)


def test_oversize_input_is_refused():
    with pytest.raises(InputError) as exc:
        build_graph("mermaid", "A[x] --> B[y]\n" * 40000)
    assert "too_large" in str(exc.value)


def test_summary_grammar_agrees_with_counts():
    """The summary is the headline of every report; "1 have" reads as broken."""
    from core.grounding import summarize
    from core.models import Edge, EdgeVerdict

    def v(label, a="s3", b="lambda"):
        return EdgeVerdict(src_service=a, dst_service=b, edge=Edge(src="x", dst="y"), label=label)

    one = summarize([v("GROUNDED"), v("UNPRECEDENTED_IN_CORPUS", "s3", "step_functions")], [])
    assert "1 of 2 connections is well precedented" in one
    assert "1 has no precedent" in one
    assert " have " not in one

    many = summarize([v("RARE", "a", "b"), v("RARE", "c", "d")], [])
    assert "0 of 2 connections are well precedented" in many
    assert "2 appear in only one or two patterns" in many
