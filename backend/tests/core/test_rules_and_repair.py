"""Tests for the integration rules table and the repair engine."""

from __future__ import annotations

import pytest

from core.models import Evidence
from core.repair import NO_PATH_MESSAGE, build_corpus_graph, explain, find_repairs
from core.rules import Rule, RuleTable, load_rules


# ---------------------------------------------------------------------------
# Rules table
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def table():
    return load_rules()


def test_the_shipped_table_loads_and_is_structurally_sound(table):
    assert len(table) > 0
    problems = list(table.problems())
    assert problems == [], f"rule table problems: {problems}"


def test_every_row_carries_a_doc_url(table):
    missing = [r.rule_id for r in table.rules if not r.doc_url]
    assert missing == [], f"rules with no doc URL: {missing}"


def test_unverified_rules_never_produce_an_unsupported_verdict(table):
    """The honesty rule, enforced in code rather than on a checklist.

    Nothing in the shipped table has initials yet, so nothing may downgrade an
    edge to UNSUPPORTED. The rows are present and reportable, but inert.
    """
    assert table.stats()["verified"] == 0
    assert table.unsupported_rule("s3", "step_functions") is None


def test_a_verified_rule_does_produce_a_verdict():
    t = RuleTable([
        Rule({
            "rule_id": "T1", "src_service": "s3", "dst_service": "step_functions",
            "direct_supported": "false",
            "doc_url": "https://docs.aws.amazon.com/x", "verified_by": "rc",
            "note": "verified for the test",
        })
    ])
    ref = t.unsupported_rule("s3", "step_functions")
    assert ref is not None
    assert ref.rule_id == "T1"
    assert ref.doc_url.startswith("https://")


def test_a_rule_without_initials_is_inert_even_with_a_doc_url():
    t = RuleTable([
        Rule({
            "rule_id": "T2", "src_service": "sns", "dst_service": "dynamodb",
            "direct_supported": "false",
            "doc_url": "https://docs.aws.amazon.com/x", "verified_by": "",
        })
    ])
    assert t.unsupported_rule("sns", "dynamodb") is None
    assert t.stats()["pending_unsupported"] == 1


def test_repair_still_avoids_unverified_unsupported_edges(table):
    """Verification gates what we SAY, not what we route through.

    Claiming an edge is unsupported needs a human signature. Quietly declining
    to suggest it as a repair hop does not, and routing through something we
    believe is impossible would be worse than finding no path.
    """
    assert table.is_known_unsupported("s3", "step_functions") is True


def test_the_demo_edge_is_present_and_unsupported(table):
    """S3 to Step Functions is the worked example in the plan."""
    assert table.supports("s3", "step_functions") is False
    assert table.supports("s3", "eventbridge") is True
    assert table.supports("eventbridge", "step_functions") is True


def test_unknown_pairs_return_none(table):
    assert table.supports("athena", "polly") is None


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------


# A small corpus shaped like the real one.
PAIRS = [
    ("s3", "lambda", 18),
    ("s3", "eventbridge", 12),
    ("s3", "sqs", 4),
    ("eventbridge", "step_functions", 12),
    ("eventbridge", "lambda", 28),
    ("lambda", "step_functions", 4),
    ("sqs", "lambda", 31),
    ("lambda", "dynamodb", 43),
    ("sqs", "step_functions", 1),
]


def _graph(unsupported=frozenset()):
    return build_corpus_graph(PAIRS, lambda a, b: (a, b) in unsupported)


def test_finds_the_two_hop_repair_for_the_demo_edge():
    g = _graph()
    paths = find_repairs(g, "s3", "step_functions")
    assert paths, "expected at least one repair path"
    assert paths[0].services == ["s3", "eventbridge", "step_functions"]
    assert paths[0].hops == 2


def test_well_precedented_hops_are_preferred_over_rare_ones():
    """Plain shortest path would accept a one-pattern hop. Weighting must not."""
    g = _graph()
    best = find_repairs(g, "s3", "step_functions")[0]
    assert "eventbridge" in best.services
    assert min(best.hop_counts) >= 12


def test_unsupported_hops_are_excluded_from_routing():
    g = _graph(unsupported={("s3", "eventbridge")})
    paths = find_repairs(g, "s3", "step_functions")
    for p in paths:
        assert ("s3", "eventbridge") not in list(zip(p.services, p.services[1:]))


def test_hop_limit_is_respected():
    g = _graph()
    for p in find_repairs(g, "s3", "dynamodb", max_hops=2):
        assert p.hops <= 2


def test_no_path_returns_empty_rather_than_inventing_one():
    g = _graph()
    assert find_repairs(g, "dynamodb", "s3") == []


def test_unknown_service_returns_no_path():
    g = _graph()
    assert find_repairs(g, "quantum_ledger", "lambda") == []


def test_self_repair_is_not_attempted():
    g = _graph()
    assert find_repairs(g, "lambda", "lambda") == []


def test_evidence_is_attached_per_hop():
    g = _graph()

    def facts(a, b):
        return 1, [Evidence(pattern_id=f"{a}-{b}", title=f"{a} to {b}", url="https://x")]

    p = find_repairs(g, "s3", "step_functions", facts)[0]
    assert len(p.hop_evidence) == p.hops
    assert p.hop_evidence[0][0].pattern_id == "s3-eventbridge"


def test_paths_are_ordered_cheapest_first():
    g = _graph()
    paths = find_repairs(g, "s3", "step_functions", max_paths=2)
    if len(paths) > 1:
        assert paths[0].total_weight <= paths[1].total_weight


def test_explain_reads_as_a_sentence():
    g = _graph()
    text = explain(find_repairs(g, "s3", "step_functions")[0])
    assert "s3 then eventbridge then step_functions" in text
    assert text.endswith(".")


def test_no_path_message_does_not_overclaim():
    """Absence from the corpus is never evidence of impossibility."""
    assert "does not mean none exists" in NO_PATH_MESSAGE


def test_graph_excludes_zero_count_and_self_loops():
    g = build_corpus_graph([("a", "b", 0), ("c", "c", 5), ("a", "c", 2)])
    assert not g.has_edge("a", "b")
    assert not g.has_edge("c", "c")
    assert g.has_edge("a", "c")


def test_a_rare_direct_edge_is_not_offered_as_its_own_alternative():
    """sqs -> step_functions is in the corpus once, so it is RARE. Suggesting
    "sqs -> step_functions" as the way around it would be circular."""
    g = _graph()
    paths = find_repairs(g, "sqs", "step_functions")
    assert paths and all(p.hops >= 2 for p in paths)
    assert paths[0].services == ["sqs", "lambda", "step_functions"]


def test_a_cheap_path_after_an_over_long_one_is_still_found():
    """Paths arrive cheapest first, not shortest first, so exceeding the hop
    limit once must not end the search."""
    # four common hops cost about 4.0; three one-pattern hops cost 6.0
    pairs = [("a", "b", 1000), ("b", "c", 1000), ("c", "d", 1000), ("d", "z", 1000),
             ("a", "x", 1), ("x", "y", 1), ("y", "z", 1)]
    g = build_corpus_graph(pairs)
    paths = find_repairs(g, "a", "z", max_hops=3)
    assert [p.services for p in paths] == [["a", "x", "y", "z"]]
