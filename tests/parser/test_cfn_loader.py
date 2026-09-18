"""Tests for the CFN-tolerant loader.

The contract is "never crash". Every case here that a stock yaml.SafeLoader
would reject must still produce usable resources.
"""

from __future__ import annotations

import pytest

from core.cfn_loader import (
    TemplateParseError,
    loads,
    referenced_logical_ids,
)

SAM_TEMPLATE = """
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Resources:
  Bucket:
    Type: AWS::S3::Bucket
  Table:
    Type: AWS::DynamoDB::Table
    Properties:
      TableName: !Sub '${AWS::StackName}-items'
  ProcessFn:
    Type: AWS::Serverless::Function
    Properties:
      Handler: app.handler
      Environment:
        Variables:
          TABLE: !Ref Table
          BUCKET_ARN: !GetAtt Bucket.Arn
      Events:
        Upload:
          Type: S3
          Properties:
            Bucket: !Ref Bucket
            Events: s3:ObjectCreated:*
"""


def test_short_form_tags_do_not_crash():
    t = loads(SAM_TEMPLATE)
    assert t.ok
    assert set(t.resources) == {"Bucket", "Table", "ProcessFn"}


def test_detects_sam_transform():
    assert loads(SAM_TEMPLATE).is_sam is True


def test_ref_expands_to_long_form():
    t = loads(SAM_TEMPLATE)
    env = t.resources["ProcessFn"]["Properties"]["Environment"]["Variables"]
    assert env["TABLE"] == {"Ref": "Table"}


def test_getatt_scalar_becomes_a_pair():
    t = loads(SAM_TEMPLATE)
    env = t.resources["ProcessFn"]["Properties"]["Environment"]["Variables"]
    assert env["BUCKET_ARN"] == {"Fn::GetAtt": ["Bucket", "Arn"]}


def test_iter_resources_yields_type_and_properties():
    t = loads(SAM_TEMPLATE)
    by_id = {lid: (rt, props) for lid, rt, props in t.iter_resources()}
    assert by_id["Bucket"][0] == "AWS::S3::Bucket"
    assert by_id["ProcessFn"][1]["Handler"] == "app.handler"


def test_json_templates_load():
    t = loads('{"Resources": {"B": {"Type": "AWS::S3::Bucket"}}}')
    assert t.resource_type("B") == "AWS::S3::Bucket"


def test_unknown_tag_is_tolerated_not_fatal():
    t = loads(
        "Resources:\n"
        "  Q:\n"
        "    Type: AWS::SQS::Queue\n"
        "    Properties:\n"
        "      Weird: !SomeFutureTag foo\n"
    )
    assert t.resource_type("Q") == "AWS::SQS::Queue"


def test_malformed_resource_warns_but_keeps_the_rest():
    t = loads(
        "Resources:\n"
        "  Good:\n"
        "    Type: AWS::S3::Bucket\n"
        "  Bad: 'just a string'\n"
        "  NoType:\n"
        "    Properties: {}\n"
    )
    assert t.resource_type("Good") == "AWS::S3::Bucket"
    assert len(list(t.iter_resources())) == 1
    assert len(t.warnings) == 2


def test_missing_resources_section_warns_rather_than_raising():
    t = loads("Parameters:\n  Foo:\n    Type: String\n")
    assert t.ok is False
    assert t.resources == {}
    assert any("no Resources" in w for w in t.warnings)


def test_invalid_yaml_reports_a_location():
    with pytest.raises(TemplateParseError) as exc:
        loads("Resources:\n  A: [unclosed\n")
    assert exc.value.line is not None
    assert exc.value.as_api_error()["error"] == "parse_error"


def test_empty_template_raises():
    with pytest.raises(TemplateParseError):
        loads("")


# --------------------------------------------------------------------------
# reference resolution
# --------------------------------------------------------------------------


def test_collects_refs_and_getatts():
    t = loads(SAM_TEMPLATE)
    refs = referenced_logical_ids(t.resources["ProcessFn"]["Properties"])
    assert {"Table", "Bucket"} <= refs


def test_sub_string_references_are_found():
    assert referenced_logical_ids({"Fn::Sub": "arn:${MyTable}/index"}) == {"MyTable"}


def test_pseudo_parameters_are_not_logical_ids():
    assert referenced_logical_ids({"Fn::Sub": "${AWS::StackName}-x"}) == set()
    assert referenced_logical_ids({"Ref": "AWS::Region"}) == set()


def test_deeply_nested_refs_are_found():
    blob = {
        "Statement": [
            {"Action": "s3:*", "Resource": [{"Fn::GetAtt": ["DeepBucket", "Arn"]}]},
            {"Condition": {"X": {"Ref": "DeepTable"}}},
        ]
    }
    assert referenced_logical_ids(blob) == {"DeepBucket", "DeepTable"}


def test_sub_with_variable_map_finds_both_halves():
    expr = {"Fn::Sub": ["${Alias}/${Direct}", {"Alias": {"Ref": "Aliased"}}]}
    found = referenced_logical_ids(expr)
    assert "Direct" in found
    assert "Aliased" in found
