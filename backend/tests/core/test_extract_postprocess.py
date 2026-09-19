"""Tests for the deterministic post-processor.

The fixtures here are not invented. They are the actual raw outputs recorded on
2026-09-18 from qwen2.5:7b and llama3.2:3b on the two pilot designs, including
the errors both models made on every run.
"""

from __future__ import annotations

import pytest

from core.extract_postprocess import postprocess
from core.models import ArchitectureGraph, Edge, Node


def _graph(pairs: list[tuple[str, str]], services: dict[str, str]) -> ArchitectureGraph:
    nodes = [
        Node(id=nid, label=nid, service=svc, canon_method="llm")
        for nid, svc in services.items()
    ]
    edges = [Edge(src=a, dst=b, relation="flows_to") for a, b in pairs]
    return ArchitectureGraph(input_type="prose", nodes=nodes, edges=edges)


def _service_pairs(g: ArchitectureGraph) -> set[tuple[str, str]]:
    return g.service_edges()


# ---------------------------------------------------------------------------
# Case 1: receipt pipeline. Both models emitted textract -> dynamodb and lost
# the extractor Lambda's write. qwen2.5:7b additionally wired cognito to the
# extractor Lambda instead of to the API.
# ---------------------------------------------------------------------------

CASE1_SERVICES = {
    "apigateway": "apigateway",
    "lambda_presign": "lambda",
    "s3": "s3",
    "lambda_extractor": "lambda",
    "textract": "textract",
    "dynamodb": "dynamodb",
    "lambda_stream": "lambda",
    "eventbridge": "eventbridge",
    "sqs": "sqs",
    "lambda_notifier": "lambda",
    "ses": "ses",
    "cognito": "cognito",
}

CASE1_RAW = [
    ("apigateway", "lambda_presign"),
    ("lambda_presign", "s3"),
    ("s3", "lambda_extractor"),
    ("lambda_extractor", "textract"),
    ("textract", "dynamodb"),        # wrong actor
    ("dynamodb", "lambda_stream"),
    ("lambda_stream", "eventbridge"),
    ("eventbridge", "sqs"),
    ("sqs", "lambda_notifier"),
    ("lambda_notifier", "ses"),
    ("cognito", "lambda_extractor"),  # wrong auth target
]


def test_case1_actor_confusion_is_reattached_to_the_caller():
    g = postprocess(_graph(CASE1_RAW, CASE1_SERVICES))
    pairs = _service_pairs(g)
    assert ("textract", "dynamodb") not in pairs, "call-only service must not emit"
    assert ("lambda", "dynamodb") in pairs, "the write belongs to the calling Lambda"


def test_case1_auth_is_redirected_to_the_api_surface():
    g = postprocess(_graph(CASE1_RAW, CASE1_SERVICES))
    pairs = _service_pairs(g)
    assert ("cognito", "apigateway") in pairs
    assert ("cognito", "lambda") not in pairs


def test_case1_reaches_ground_truth():
    expected = {
        ("apigateway", "lambda"),
        ("lambda", "s3"),
        ("s3", "lambda"),
        ("lambda", "textract"),
        ("lambda", "dynamodb"),
        ("dynamodb", "lambda"),
        ("lambda", "eventbridge"),
        ("eventbridge", "sqs"),
        ("sqs", "lambda"),
        ("lambda", "ses"),
        ("cognito", "apigateway"),
    }
    g = postprocess(_graph(CASE1_RAW, CASE1_SERVICES))
    assert _service_pairs(g) == expected


# ---------------------------------------------------------------------------
# Case 2: photo pipeline. This is the design that exposed the adjacency bug:
# CloudFront is listed before API Gateway, so a naive auth rule wires Cognito
# to CloudFront.
# ---------------------------------------------------------------------------

CASE2_SERVICES = {
    "cloudfront": "cloudfront",
    "apigateway": "apigateway",
    "cognito": "cognito",
    "lambda_search": "lambda",
    "opensearch": "opensearch",
    "lambda_ingest": "lambda",
    "s3": "s3",
    "sqs": "sqs",
    "lambda_thumb": "lambda",
    "rekognition": "rekognition",
    "dynamodb": "dynamodb",
    "step_functions": "step_functions",
}

CASE2_RAW = [
    ("cloudfront", "apigateway"),
    ("apigateway", "cognito"),        # reversed auth
    ("cognito", "lambda_search"),     # wrong auth target
    ("lambda_search", "opensearch"),
    ("s3", "sqs"),
    ("lambda_ingest", "sqs"),         # reversed pull direction
    ("sqs", "step_functions"),
    ("step_functions", "lambda_thumb"),
    ("lambda_thumb", "rekognition"),
    ("rekognition", "dynamodb"),      # wrong actor
]


def test_case2_auth_does_not_attach_to_cloudfront():
    """Regression: choose the API surface by adjacency, never by list order."""
    g = postprocess(_graph(CASE2_RAW, CASE2_SERVICES))
    pairs = _service_pairs(g)
    assert ("cognito", "cloudfront") not in pairs
    assert ("cognito", "apigateway") in pairs


def test_case2_call_only_ml_service_does_not_write():
    g = postprocess(_graph(CASE2_RAW, CASE2_SERVICES))
    pairs = _service_pairs(g)
    assert ("rekognition", "dynamodb") not in pairs
    assert ("lambda", "dynamodb") in pairs


def test_case2_pull_direction_is_normalised():
    g = postprocess(_graph(CASE2_RAW, CASE2_SERVICES))
    pairs = _service_pairs(g)
    assert ("sqs", "lambda") in pairs
    assert ("lambda", "sqs") not in pairs


# ---------------------------------------------------------------------------
# Rule D and general hygiene
# ---------------------------------------------------------------------------


def test_corpus_direction_repair_flips_unprecedented_edge():
    services = {"a": "lambda", "b": "s3"}
    counts = {("s3", "lambda"): 40, ("lambda", "s3"): 0}
    g = postprocess(
        _graph([("a", "b")], services),
        count_fn=lambda x, y: counts.get((x, y), 0),
    )
    assert _service_pairs(g) == {("s3", "lambda")}
    assert any("no pattern in the corpus" in w for w in g.warnings)


def test_corpus_direction_repair_is_skipped_without_count_fn():
    services = {"a": "lambda", "b": "s3"}
    g = postprocess(_graph([("a", "b")], services))
    assert _service_pairs(g) == {("lambda", "s3")}


def test_self_loops_and_duplicates_are_removed():
    services = {"a": "lambda", "b": "dynamodb"}
    g = postprocess(_graph([("a", "a"), ("a", "b"), ("a", "b")], services))
    assert len(g.edges) == 1
    assert any("self-loop" in w for w in g.warnings)


def test_every_change_is_explained_in_warnings():
    g = postprocess(_graph(CASE1_RAW, CASE1_SERVICES))
    assert g.warnings, "the confirmation UI needs a reason for every rewrite"
    assert all(isinstance(w, str) and w.strip() for w in g.warnings)


def test_postprocess_does_not_mutate_its_input():
    original = _graph(CASE1_RAW, CASE1_SERVICES)
    before = [(e.src, e.dst) for e in original.edges]
    postprocess(original)
    assert [(e.src, e.dst) for e in original.edges] == before
