"""Repair must suggest routes that mean something, not just routes that exist.

Written after the engine proposed `cognito -> lambda -> apigateway` for a
Cognito authorizer edge. Every hop was well precedented and the path was
nonsense: Cognito authorizes an API, it does not carry traffic to one.

Two independent guards, because they catch different mistakes:

* `repairable()` decides whether the EDGE should be offered a route at all
* `is_relay` decides which services may sit in the MIDDLE of one
"""

from __future__ import annotations

import pytest

from core.audit import CONFIGURATION_RELATIONS, repairable
from core.models import Edge, EdgeVerdict
from core.repair import build_corpus_graph, find_repairs
from core.vocabulary import load as load_vocab


@pytest.fixture(scope="module")
def vocab():
    return load_vocab()


def _verdict(src, dst, relation="flows_to", label="UNPRECEDENTED_IN_CORPUS"):
    return EdgeVerdict(
        src_service=src, dst_service=dst,
        edge=Edge(src="a", dst="b", relation=relation),
        label=label, count=0,
    )


# ---------------------------------------------------------------------------
# Which edges deserve a repair
# ---------------------------------------------------------------------------


def test_an_auth_source_is_never_offered_a_route(vocab):
    """The bug that started this. Cognito has no traffic to reroute."""
    assert repairable(_verdict("cognito", "apigateway"), vocab) is False


def test_a_call_only_source_is_never_offered_a_route(vocab):
    """Textract never emits, so any path out of it is a graph artefact."""
    assert repairable(_verdict("textract", "dynamodb"), vocab) is False


def test_configuration_relations_get_no_flow_repair(vocab):
    """Routing around an authorizer does not authorize anything."""
    v = _verdict("apigateway", "lambda", relation="configured_with")
    assert repairable(v, vocab) is False
    assert "configured_with" in CONFIGURATION_RELATIONS


def test_a_genuine_flow_edge_is_still_repairable(vocab):
    assert repairable(_verdict("s3", "step_functions", "triggers"), vocab) is True


def test_grounded_edges_need_no_repair(vocab):
    assert repairable(_verdict("lambda", "dynamodb", label="GROUNDED"), vocab) is False


def test_rare_edges_are_repairable(vocab):
    """"Works, but almost nobody does it" is exactly when an alternative helps."""
    assert repairable(_verdict("s3", "sqs", label="RARE"), vocab) is True


# ---------------------------------------------------------------------------
# Which services may sit in the middle
# ---------------------------------------------------------------------------


# A deliberately hostile corpus: the cheap path runs through a service that
# cannot forward anything.
HOSTILE = [
    ("apigateway", "secrets_manager", 50),   # a function reads a secret
    ("secrets_manager", "dynamodb", 50),     # nonsense, but suppose it appeared
    ("apigateway", "lambda", 5),
    ("lambda", "dynamodb", 5),
]


def test_a_non_relay_intermediate_is_rejected_even_when_cheapest(vocab):
    g = build_corpus_graph(HOSTILE)
    relays = vocab.with_role("relay")

    unguarded = find_repairs(g, "apigateway", "dynamodb", max_paths=1)
    assert unguarded[0].services == ["apigateway", "secrets_manager", "dynamodb"], \
        "precondition: the nonsense path really is the cheapest"

    guarded = find_repairs(g, "apigateway", "dynamodb", max_paths=1,
                           is_relay=lambda s: s in relays)
    assert guarded[0].services == ["apigateway", "lambda", "dynamodb"]


def test_endpoints_are_exempt_from_the_relay_rule(vocab):
    """The endpoints are the edge the user drew; only the middle is filtered."""
    g = build_corpus_graph([("lambda", "sqs", 10), ("sqs", "textract", 10)])
    relays = vocab.with_role("relay")
    paths = find_repairs(g, "lambda", "textract", is_relay=lambda s: s in relays)
    assert paths and paths[0].services == ["lambda", "sqs", "textract"]


def test_no_acceptable_path_returns_empty_rather_than_a_bad_one(vocab):
    g = build_corpus_graph([("apigateway", "secrets_manager", 9),
                            ("secrets_manager", "dynamodb", 9)])
    relays = vocab.with_role("relay")
    assert find_repairs(g, "apigateway", "dynamodb",
                        is_relay=lambda s: s in relays) == []


def test_relay_membership_is_coherent(vocab):
    """Nothing that cannot emit should be marked as able to forward."""
    relays = vocab.with_role("relay")
    assert not (relays & vocab.call_only), "a call-only service cannot forward"
    assert not (relays & vocab.auth), "an auth service does not carry traffic"
    for s in ("lambda", "sqs", "sns", "eventbridge", "s3", "dynamodb", "step_functions"):
        assert s in relays, f"{s} should be able to forward"
