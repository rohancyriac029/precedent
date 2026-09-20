"""Tests for the Mermaid parser and canonicalisation.

Mermaid is the demo's primary input, because it is what an LLM hands you when
you ask it to design an architecture.
"""

from __future__ import annotations

import pytest

from core.canonicalize import load as load_canon, normalise
from core.mermaid_parser import parse_mermaid


def edges(g) -> set[tuple[str, str]]:
    return g.service_edges()


# ---------------------------------------------------------------------------
# Canonicalisation
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def canon():
    return load_canon()


@pytest.mark.parametrize("label,expected", [
    ("S3", "s3"),
    ("Amazon S3", "s3"),
    ("Upload Bucket", "s3"),
    ("Lambda Function", "lambda"),
    ("Order Processor Lambda", "lambda"),
    ("DynamoDB Table", "dynamodb"),
    ("Orders Queue", "sqs"),
    ("Notification Topic", "sns"),
    ("Event Bus", "eventbridge"),
    ("API Gateway", "apigateway"),
    ("REST API", "apigateway"),
    ("State Machine", "step_functions"),
    ("Step Functions Workflow", "step_functions"),
    ("Cognito User Pool", "cognito"),
    ("CloudFront CDN", "cloudfront"),
])
def test_common_labels_resolve(canon, label, expected):
    service, method, _ = canon.resolve(label)
    assert service == expected, f"{label!r} resolved to {service}"
    assert method in {"alias", "fuzzy"}


def test_longest_alias_wins(canon):
    """"Kinesis Firehose" must not be swallowed by the shorter "kinesis"."""
    assert canon.resolve("Kinesis Firehose")[0] == "firehose"
    assert canon.resolve("Kinesis Data Stream")[0] == "kinesis_streams"


def test_filler_words_are_ignored(canon):
    assert canon.resolve("The Main Production Lambda Function")[0] == "lambda"


def test_unrecognised_label_becomes_unknown_not_a_guess(canon):
    """Guessing would attach a real precedent count to the wrong service."""
    service, method, _ = canon.resolve("Zorbulator Prime")
    assert service == "unknown"
    assert method == "unknown"


def test_empty_label_is_unknown(canon):
    assert canon.resolve("")[0] == "unknown"
    assert canon.resolve("   ")[0] == "unknown"


def test_normalise_strips_noise():
    assert normalise("  AWS  Lambda-Function!! ") == "lambda function"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_simple_flowchart():
    g = parse_mermaid("""
flowchart LR
  A[Upload Bucket] --> B[Extractor Lambda]
  B --> C[(Orders Table)]
""")
    assert ("s3", "lambda") in edges(g)
    assert ("lambda", "dynamodb") in edges(g)


def test_edge_label_sets_the_relation():
    g = parse_mermaid("""
flowchart LR
  A[Order Queue] -->|triggers| B[Worker Lambda]
  B -->|writes| C[Orders Table]
""")
    rels = {e.relation for e in g.edges}
    assert "triggers" in rels
    assert "writes" in rels


def test_chained_edges():
    g = parse_mermaid("""
flowchart LR
  A[Upload Bucket] --> B[Lambda] --> C[Event Bus]
""")
    assert ("s3", "lambda") in edges(g)
    assert ("lambda", "eventbridge") in edges(g)


def test_various_node_shapes():
    g = parse_mermaid("""
flowchart TD
  A([API Gateway]) --> B[[Auth Lambda]]
  B --> C[(User Table)]
  C --> D{{Event Bus}}
""")
    assert ("apigateway", "lambda") in edges(g)
    assert ("lambda", "dynamodb") in edges(g)
    assert ("dynamodb", "eventbridge") in edges(g)


def test_subgraphs_are_flattened_not_treated_as_services():
    g = parse_mermaid("""
flowchart LR
  subgraph Ingest
    A[Upload Bucket] --> B[Ingest Lambda]
  end
  subgraph Store
    C[(Orders Table)]
  end
  B --> C
""")
    assert ("s3", "lambda") in edges(g)
    assert ("lambda", "dynamodb") in edges(g)
    assert all(n.id not in {"Ingest", "Store"} for n in g.nodes)


def test_reverse_arrow_flips_direction():
    g = parse_mermaid("""
flowchart LR
  A[Worker Lambda] <-- B[Order Queue]
""")
    assert ("sqs", "lambda") in edges(g)


def test_dotted_and_thick_arrows_are_edges():
    g = parse_mermaid("""
flowchart LR
  A[Upload Bucket] -.-> B[Lambda]
  B ==> C[Event Bus]
""")
    assert ("s3", "lambda") in edges(g)
    assert ("lambda", "eventbridge") in edges(g)


def test_unmapped_component_warns_and_does_not_invent_a_service():
    g = parse_mermaid("""
flowchart LR
  A[Zorbulator Prime] --> B[Lambda]
""")
    assert any(n.service == "unknown" for n in g.nodes)
    assert any("did not match a known AWS service" in w for w in g.warnings)


def test_styling_directives_are_ignored():
    g = parse_mermaid("""
flowchart LR
  classDef big fill:#f9f
  A[Upload Bucket] --> B[Lambda]
  style A fill:#bbf
  linkStyle 0 stroke:#333
""")
    assert edges(g) == {("s3", "lambda")}


def test_comments_are_stripped():
    g = parse_mermaid("""
flowchart LR
  %% this is a comment
  A[Upload Bucket] --> B[Lambda]  %% trailing comment
""")
    assert ("s3", "lambda") in edges(g)


def test_missing_header_warns_but_still_parses():
    g = parse_mermaid("A[Upload Bucket] --> B[Lambda]")
    assert ("s3", "lambda") in edges(g)
    assert any("No 'flowchart'" in w for w in g.warnings)


def test_duplicate_edges_are_collapsed():
    g = parse_mermaid("""
flowchart LR
  A[Bucket] --> B[Lambda]
  A --> B
""")
    assert len(g.edges) == 1


def test_self_loops_are_dropped():
    g = parse_mermaid("""
flowchart LR
  A[Lambda] --> A
""")
    assert g.edges == []


def test_the_demo_shaped_diagram():
    """The failing architecture from the plan: S3 straight to Step Functions,
    plus SNS and EventBridge doing the same job."""
    g = parse_mermaid("""
flowchart LR
  S3[Document Bucket] --> SFN[Processing State Machine]
  SFN --> L[Extract Lambda]
  L --> DDB[(Results Table)]
  L --> SNS[Alert Topic]
  L --> EB[Event Bus]
  SNS --> Q[Notify Queue]
  EB --> Q
""")
    e = edges(g)
    assert ("s3", "step_functions") in e
    assert ("step_functions", "lambda") in e
    assert ("lambda", "dynamodb") in e
    assert ("sns", "sqs") in e
    assert ("eventbridge", "sqs") in e


def test_a_two_letter_alias_matches_inside_a_longer_label(canon):
    """"S3 Archive" is a bucket. The alias "s3" is short, but it is a whole word."""
    assert canon.resolve("S3 Archive")[0] == "s3"
    assert canon.resolve("Raw S3 bucket for uploads")[0] == "s3"
