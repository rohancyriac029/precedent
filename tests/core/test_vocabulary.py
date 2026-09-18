"""Consistency checks on data/vocabulary.yaml.

The vocabulary is edited by hand and read by the parser, the extraction enum and
the census. A contradiction in it fails silently and skews every number
downstream, so these are invariants rather than examples.
"""

from __future__ import annotations

import pytest

from core.vocabulary import load


@pytest.fixture(scope="module")
def v():
    return load()


def test_no_resource_type_is_both_mapped_and_ignored(v):
    """Regression: IAM was listed as a service AND as ignored plumbing.

    The mapping won silently, so IAM roles became architecture nodes and every
    template grew a meaningless lambda -> iam edge.
    """
    both = sorted(rt for rt in v.mapped_resource_types if v.is_ignored(rt))
    assert both == [], f"both mapped and ignored: {both}"


def test_edge_constructs_and_sub_resources_are_never_node_types(v):
    """Regression: sub-resources became nodes, so one REST API produced four.

    A type may map to a parent service for census purposes while still being
    excluded from the graph. is_node_type is the single place that decides.
    """
    for rt in v.edge_constructs | v.sub_resource_types:
        assert not v.is_node_type(rt), f"{rt} would become an architecture node"


def test_primary_resource_types_are_node_types(v):
    for rt in (
        "AWS::Serverless::Function",
        "AWS::DynamoDB::Table",
        "AWS::S3::Bucket",
        "AWS::ApiGateway::RestApi",
        "AWS::ApiGatewayV2::Api",
        "AWS::Cognito::UserPool",
    ):
        assert v.is_node_type(rt), f"{rt} should be an architecture node"


def test_every_service_has_a_title(v):
    missing = [s for s in v.ids if not v.title(s) or v.title(s) == s]
    assert missing == [], f"services without a title: {missing}"


def test_capability_class_members_all_exist(v):
    unknown = sorted(
        m for members in v.capability_classes.values() for m in members if not v.has(m)
    )
    assert unknown == [], f"capability classes name unknown services: {unknown}"


def test_sdk_capable_services_all_exist(v):
    unknown = sorted(s for s in v.sdk_capable if not v.has(s))
    assert unknown == [], f"sdk_capable names unknown services: {unknown}"


def test_role_sets_are_disjoint_where_they_must_be(v):
    """A service cannot both originate nothing and be a pull source."""
    clash = sorted(v.call_only & v.pull_sources)
    assert clash == [], f"call_only and pull_source overlap: {clash}"


def test_resource_types_are_unique_across_services(v):
    seen: dict[str, str] = {}
    dupes: list[str] = []
    for sid, spec in v.services.items():
        for rt in spec.get("resource_types") or []:
            if rt in seen:
                dupes.append(f"{rt}: {seen[rt]} and {sid}")
            seen[rt] = sid
    assert dupes == [], f"resource types claimed by two services: {dupes}"


def test_ignored_entries_are_strings(v):
    """A YAML scalar ending in ':' silently parses as a mapping key."""
    assert all(isinstance(x, str) for x in v.ignored_exact)
    assert all(isinstance(x, str) for x in v.ignored_prefixes)


def test_enum_is_stable_and_includes_unknown(v):
    enum = v.enum_with_unknown()
    assert enum[-1] == "unknown"
    assert enum[:-1] == sorted(enum[:-1]), "enum must be sorted for cache stability"
    assert len(set(enum)) == len(enum)


def test_universal_infrastructure_is_not_a_service(v):
    """Log groups and IAM roles are in nearly every template.

    Modelling them as services would make lambda -> cloudwatch_logs one of the
    best-precedented edges in the corpus while carrying no information, and
    would distort the IDF weighting used to rank closest patterns.
    """
    for rt in ("AWS::Logs::LogGroup", "AWS::IAM::Role", "AWS::IAM::Policy"):
        assert v.service_for_resource_type(rt) == "unknown"
        assert v.is_ignored(rt)
