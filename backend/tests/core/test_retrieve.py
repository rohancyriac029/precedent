"""BM25 retrieval over README passages. Pure and deterministic."""

from __future__ import annotations

from core.retrieve import Bm25, cited_patterns, tokenize

CHUNKS = {
    "iot-lambda": ["When an IoT event is sent to an IoT topic, a Lambda function is invoked."],
    "kinesis-lambda": ["This pattern creates a Kinesis data stream and a Lambda consumer.",
                       "The consumer batches records from the stream."],
    "sqs-lambda": ["SQS invokes the Lambda function when new messages are available."],
    "unrelated-pattern": ["IoT Kinesis IoT Kinesis IoT Kinesis everywhere."],
}


def test_tokenize_drops_stopwords_and_punctuation():
    assert tokenize("Why is the IoT -> Kinesis link weak?") == ["iot", "kinesis", "link", "weak"]


def test_search_is_limited_to_the_candidate_patterns():
    """A pattern the audit does not cite is never retrieved, however well it matches."""
    hits = Bm25(CHUNKS).search("iot kinesis", ["iot-lambda", "kinesis-lambda"])
    assert {h.pattern_id for h in hits} <= {"iot-lambda", "kinesis-lambda"}
    assert hits and hits[0].score > 0


def test_search_ranks_the_matching_passage_first():
    hits = Bm25(CHUNKS).search("messages queue sqs", ["iot-lambda", "sqs-lambda", "kinesis-lambda"])
    assert hits[0].pattern_id == "sqs-lambda"


def test_search_is_deterministic_and_caps_per_pattern():
    idx = Bm25(CHUNKS)
    a = idx.search("stream lambda", list(CHUNKS), per_pattern=1)
    b = idx.search("stream lambda", list(CHUNKS), per_pattern=1)
    assert a == b
    assert len({h.pattern_id for h in a}) == len(a)


def test_cited_patterns_collects_evidence_routes_and_closest_in_order():
    report = {
        "edges": [{
            "evidence": [{"pattern_id": "a"}],
            "repairs": [{"hop_evidence": [[{"pattern_id": "b"}], [{"pattern_id": "a"}]]}],
        }],
        "closest_patterns": [{"pattern_id": "c"}],
    }
    assert cited_patterns(report) == ["a", "b", "c"]


def test_passages_with_no_matching_term_are_not_returned():
    assert Bm25(CHUNKS).search("monthly pricing", list(CHUNKS)) == []
