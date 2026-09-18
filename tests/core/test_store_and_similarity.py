"""Tests for the edge store interface and closest-pattern ranking."""

from __future__ import annotations

import sqlite3

import pytest

from core.models import ArchitectureGraph, Edge, Evidence, Node
from core.similarity import PatternIndex, closest_patterns
from core.store import DictStore, SqliteStore


# ---------------------------------------------------------------------------
# SqliteStore
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """A tiny index shaped exactly like the real one."""
    path = tmp_path / "t.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE patterns (id TEXT PRIMARY KEY, title TEXT, framework TEXT,
          url TEXT, readme TEXT, services TEXT, parse_status TEXT, parse_errors TEXT);
        CREATE TABLE edges (pattern_id TEXT, src_service TEXT, dst_service TEXT,
          relation TEXT, confidence TEXT, source_construct TEXT);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    conn.executemany("INSERT INTO patterns VALUES (?,?,?,?,?,?,?,?)", [
        ("p1", "S3 to Lambda", "SAM", "https://x/p1", "", "s3,lambda", "ok", ""),
        ("p2", "S3 to Lambda too", "SAM", "https://x/p2", "", "s3,lambda", "ok", ""),
        ("p3", "Firehose to S3", "SAM", "https://x/p3", "", "firehose,s3", "ok", ""),
    ])
    conn.executemany("INSERT INTO edges VALUES (?,?,?,?,?,?)", [
        ("p1", "s3", "lambda", "triggers", "strong", "x"),
        ("p1", "s3", "lambda", "triggers", "strong", "y"),   # same pair twice
        ("p1", "lambda", "dynamodb", "writes", "strong", "z"),
        ("p2", "s3", "lambda", "triggers", "strong", "x"),
        ("p3", "firehose", "s3", "writes", "strong", "x"),
        ("p3", "lambda", "dynamodb", "writes", "weak", "env"),  # weak, excluded
    ])
    conn.executemany("INSERT INTO meta VALUES (?,?)", [("corpus_commit", "abc123")])
    conn.commit()
    conn.close()
    return path


def test_count_is_distinct_patterns_not_rows(db):
    """p1 lists s3 -> lambda twice. That is one piece of evidence, not two."""
    store = SqliteStore(db)
    assert store.facts("s3", "lambda").count == 2  # p1 and p2


def test_evidence_carries_title_and_url(db):
    ev = SqliteStore(db).facts("s3", "lambda").evidence
    assert ev[0].title == "S3 to Lambda"
    assert ev[0].url.startswith("https://")


def test_weak_edges_are_excluded_from_counts(db):
    """p3 has a weak lambda -> dynamodb, only p1 has a strong one."""
    assert SqliteStore(db).facts("lambda", "dynamodb").count == 1


def test_unknown_pair_returns_zero_not_an_error(db):
    facts = SqliteStore(db).facts("polly", "athena")
    assert facts.count == 0
    assert facts.evidence == ()


def test_all_pairs_matches_the_interface(db):
    pairs = dict(((a, b), c) for a, b, c in SqliteStore(db).all_pairs())
    assert pairs[("s3", "lambda")] == 2
    assert ("lambda", "dynamodb") in pairs


def test_meta_and_pattern_lookup(db):
    store = SqliteStore(db)
    assert store.corpus_commit() == "abc123"
    assert store.pattern("p3")["title"] == "Firehose to S3"
    assert store.pattern("nope") is None


def test_missing_index_fails_with_a_useful_message(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        SqliteStore(tmp_path / "absent.sqlite")
    assert "tasks.py index" in str(exc.value)


def test_dict_store_satisfies_the_same_interface():
    s = DictStore({("a", "b"): 3}, {("a", "b"): [Evidence(pattern_id="p", title="t", url="u")]})
    assert s.facts("a", "b").count == 3
    assert s.all_pairs() == [("a", "b", 3)]


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------


# A corpus where one edge is ubiquitous and one is rare.
PATTERNS = {
    **{f"common{i}": {("apigateway", "lambda")} for i in range(20)},
    "rare1": {("firehose", "s3")},
    "mixed": {("apigateway", "lambda"), ("firehose", "s3")},
    "big": {("apigateway", "lambda"), ("lambda", "dynamodb"), ("lambda", "sqs")},
}
META = {p: {"title": p.title(), "url": f"https://x/{p}"} for p in PATTERNS}


@pytest.fixture
def index():
    return PatternIndex(PATTERNS, META)


def test_rare_shared_edge_outranks_a_ubiquitous_one(index):
    """apigateway -> lambda is in 22 patterns and says almost nothing.
    firehose -> s3 is in 2 and is real evidence.

    All the `common*` patterns are fully contained too, so they tie on
    containment. The rare match must still come first.
    """
    results = index.closest({("apigateway", "lambda"), ("firehose", "s3")})
    ids = [r.pattern_id for r in results]
    assert ids.index("rare1") < ids.index("common0"), ids[:5]


def test_a_pattern_of_only_common_edges_is_still_rankable(index):
    """Regression: unsmoothed IDF gave a ubiquitous edge weight 0, so a pattern
    containing only that edge had a zero denominator and was dropped. A design
    that IS just an API and a Lambda then matched nothing at all."""
    results = index.closest({("apigateway", "lambda")})
    assert results, "a common-edge design must still find its closest patterns"
    assert any(r.pattern_id.startswith("common") for r in results)


def test_containment_prefers_a_fully_covered_pattern(index):
    """A two-edge pattern fully contained beats a three-edge one partly covered.

    limit is raised past the default because `big` scores low enough to fall
    outside the top five once every fully-contained pattern is ahead of it.
    """
    results = index.closest({("apigateway", "lambda"), ("firehose", "s3")}, limit=50)
    ids = [r.pattern_id for r in results]
    assert ids.index("mixed") < ids.index("big")
    assert results[ids.index("big")].containment < results[ids.index("mixed")].containment


def test_shared_edges_are_reported(index):
    r = index.closest({("firehose", "s3")})[0]
    assert r.shared_edges == [("firehose", "s3")]


def test_patterns_with_no_overlap_are_excluded(index):
    assert index.closest({("polly", "athena")}) == []


def test_empty_graph_returns_nothing(index):
    assert index.closest(set()) == []


def test_ranking_is_deterministic(index):
    a = index.closest({("apigateway", "lambda"), ("firehose", "s3")})
    b = index.closest({("apigateway", "lambda"), ("firehose", "s3")})
    assert [x.pattern_id for x in a] == [x.pattern_id for x in b]


def test_limit_is_respected(index):
    assert len(index.closest({("apigateway", "lambda")}, limit=3)) == 3


def test_idf_is_never_negative(index):
    """A negative weight would reward NOT sharing a common edge."""
    assert all(w >= 0 for w in index.idf.values())
    assert index.weight(("never", "seen")) >= 0


def test_self_loops_are_ignored(index):
    assert index.closest({("lambda", "lambda")}) == []


def test_works_from_a_graph(index):
    g = ArchitectureGraph(
        input_type="mermaid",
        nodes=[Node(id="a", label="Firehose", service="firehose"),
               Node(id="b", label="Bucket", service="s3")],
        edges=[Edge(src="a", dst="b", relation="writes")],
    )
    assert closest_patterns(g, index)[0].pattern_id == "rare1"


def test_real_index_builds_from_the_store(db):
    from core.similarity import build_index_from_store
    idx = build_index_from_store(SqliteStore(db))
    assert idx.n == 3
    assert idx.closest({("firehose", "s3")})[0].pattern_id == "p3"
