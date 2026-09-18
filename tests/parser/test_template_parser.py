"""Tests for the SAM/CFN template parser, one per Appendix A rule."""

from __future__ import annotations

import pytest

from core.cfn_loader import loads
from core.template_parser import parse_template


def parse(text: str):
    return parse_template(loads(text))


def edges(result) -> set[tuple[str, str]]:
    """Service-level edges, which is the unit everything downstream counts."""
    return result.graph.service_edges()


def strong(result) -> set[tuple[str, str]]:
    g = result.graph
    return {
        (g.service_of(e.src), g.service_of(e.dst))
        for e in g.edges
        if e.confidence == "strong" and g.service_of(e.src) != g.service_of(e.dst)
    }


# --------------------------------------------------------------------------
# A1-A4: function event sources
# --------------------------------------------------------------------------


def test_a1_s3_event_source():
    r = parse("""
Resources:
  Bucket:
    Type: AWS::S3::Bucket
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Events:
        Upload:
          Type: S3
          Properties:
            Bucket: !Ref Bucket
""")
    assert ("s3", "lambda") in edges(r)


def test_a2_sqs_event_source_via_getatt():
    r = parse("""
Resources:
  Q:
    Type: AWS::SQS::Queue
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Events:
        Msg:
          Type: SQS
          Properties:
            Queue: !GetAtt Q.Arn
""")
    assert ("sqs", "lambda") in edges(r)


def test_a3_sns_event_source():
    r = parse("""
Resources:
  Topic:
    Type: AWS::SNS::Topic
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Events:
        N:
          Type: SNS
          Properties:
            Topic: !Ref Topic
""")
    assert ("sns", "lambda") in edges(r)


def test_a4_dynamodb_stream_event_source():
    r = parse("""
Resources:
  Table:
    Type: AWS::DynamoDB::Table
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Events:
        S:
          Type: DynamoDB
          Properties:
            Stream: !GetAtt Table.StreamArn
""")
    assert ("dynamodb", "lambda") in edges(r)


# --------------------------------------------------------------------------
# A5: API events, explicit and implicit
# --------------------------------------------------------------------------


def test_a5_explicit_api_is_used_when_named():
    r = parse("""
Resources:
  MyApi:
    Type: AWS::Serverless::Api
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Events:
        Get:
          Type: Api
          Properties:
            RestApiId: !Ref MyApi
            Path: /x
            Method: get
""")
    assert ("apigateway", "lambda") in edges(r)
    assert any(n.id == "MyApi" for n in r.graph.nodes)


def test_a5_implicit_api_node_is_created():
    r = parse("""
Resources:
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Events:
        Get:
          Type: Api
          Properties:
            Path: /x
            Method: get
""")
    assert ("apigateway", "lambda") in edges(r)
    assert any(n.label == "Implicit API" for n in r.graph.nodes)


# --------------------------------------------------------------------------
# A6, A7: schedule and eventbridge
# --------------------------------------------------------------------------


def test_a6_schedule_creates_an_eventbridge_source():
    r = parse("""
Resources:
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Events:
        Tick:
          Type: Schedule
          Properties:
            Schedule: rate(1 minute)
""")
    assert ("eventbridge", "lambda") in edges(r)


def test_a6_schedulev2_is_the_scheduler_service():
    r = parse("""
Resources:
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Events:
        Tick:
          Type: ScheduleV2
          Properties:
            ScheduleExpression: rate(1 minute)
""")
    assert ("eventbridge_scheduler", "lambda") in edges(r)


def test_a7_eventbridge_rule_event():
    r = parse("""
Resources:
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Events:
        E:
          Type: EventBridgeRule
          Properties:
            Pattern: {source: [app]}
""")
    assert ("eventbridge", "lambda") in edges(r)


# --------------------------------------------------------------------------
# A8-A11
# --------------------------------------------------------------------------


def test_a8_event_source_mapping():
    r = parse("""
Resources:
  Q:
    Type: AWS::SQS::Queue
  Fn:
    Type: AWS::Lambda::Function
  ESM:
    Type: AWS::Lambda::EventSourceMapping
    Properties:
      EventSourceArn: !GetAtt Q.Arn
      FunctionName: !Ref Fn
""")
    assert ("sqs", "lambda") in edges(r)


def test_a8_mapping_itself_is_never_a_node():
    r = parse("""
Resources:
  Q:
    Type: AWS::SQS::Queue
  Fn:
    Type: AWS::Lambda::Function
  ESM:
    Type: AWS::Lambda::EventSourceMapping
    Properties:
      EventSourceArn: !GetAtt Q.Arn
      FunctionName: !Ref Fn
""")
    assert "ESM" not in {n.id for n in r.graph.nodes}


def test_a9_events_rule_targets():
    r = parse("""
Resources:
  Bus:
    Type: AWS::Events::EventBus
  Fn:
    Type: AWS::Lambda::Function
  Rule:
    Type: AWS::Events::Rule
    Properties:
      EventBusName: !Ref Bus
      Targets:
        - Arn: !GetAtt Fn.Arn
          Id: t1
""")
    assert ("eventbridge", "lambda") in edges(r)
    assert "Rule" not in {n.id for n in r.graph.nodes}


def test_a10_bucket_notification_to_queue():
    r = parse("""
Resources:
  Q:
    Type: AWS::SQS::Queue
  Bucket:
    Type: AWS::S3::Bucket
    Properties:
      NotificationConfiguration:
        QueueConfigurations:
          - Event: s3:ObjectCreated:*
            Queue: !GetAtt Q.Arn
""")
    assert ("s3", "sqs") in edges(r)


def test_a10_bucket_eventbridge_configuration():
    r = parse("""
Resources:
  Bucket:
    Type: AWS::S3::Bucket
    Properties:
      NotificationConfiguration:
        EventBridgeConfiguration: {}
""")
    assert ("s3", "eventbridge") in edges(r)


def test_a11_sns_subscription():
    r = parse("""
Resources:
  Topic:
    Type: AWS::SNS::Topic
  Q:
    Type: AWS::SQS::Queue
  Sub:
    Type: AWS::SNS::Subscription
    Properties:
      TopicArn: !Ref Topic
      Endpoint: !GetAtt Q.Arn
      Protocol: sqs
""")
    assert ("sns", "sqs") in edges(r)


# --------------------------------------------------------------------------
# A12: SAM policy templates
# --------------------------------------------------------------------------


def test_a12_dynamodb_crud_policy_is_a_write():
    r = parse("""
Resources:
  Table:
    Type: AWS::DynamoDB::Table
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - DynamoDBCrudPolicy:
            TableName: !Ref Table
""")
    assert ("lambda", "dynamodb") in edges(r)


def test_a12_read_policy_uses_the_read_relation():
    r = parse("""
Resources:
  Table:
    Type: AWS::DynamoDB::Table
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - DynamoDBReadPolicy:
            TableName: !Ref Table
""")
    rel = {e.relation for e in r.graph.edges}
    assert "reads" in rel


def test_a12_step_functions_execution_policy():
    r = parse("""
Resources:
  SM:
    Type: AWS::Serverless::StateMachine
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - StepFunctionsExecutionPolicy:
            StateMachineName: !Ref SM
""")
    assert ("lambda", "step_functions") in edges(r)


def test_service_only_policy_creates_a_synthetic_node():
    """Textract is never a CFN resource, but the edge is real."""
    r = parse("""
Resources:
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - TextractDetectAnalyzePolicy: {}
""")
    assert ("lambda", "textract") in edges(r)
    node = next(n for n in r.graph.nodes if n.service == "textract")
    assert "policy" in (node.source_ref or "")


# --------------------------------------------------------------------------
# Never guess, never crash
# --------------------------------------------------------------------------


def test_literal_arn_produces_a_warning_not_an_edge():
    r = parse("""
Resources:
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Events:
        S:
          Type: SQS
          Properties:
            Queue: arn:aws:sqs:us-east-1:111122223333:external
""")
    assert edges(r) == set()
    assert any("literal ARN" in w for w in r.warnings)


def test_parameter_reference_is_not_invented():
    r = parse("""
Parameters:
  QueueArn:
    Type: String
Resources:
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Events:
        S:
          Type: SQS
          Properties:
            Queue: !Ref QueueArn
""")
    assert edges(r) == set()
    assert any("not a resource in this template" in w for w in r.warnings)


def test_environment_edges_are_weak_and_excluded_from_strong():
    r = parse("""
Resources:
  Table:
    Type: AWS::DynamoDB::Table
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Environment:
        Variables:
          TABLE: !Ref Table
""")
    assert ("lambda", "dynamodb") in edges(r)
    assert ("lambda", "dynamodb") not in strong(r)


def test_strong_edge_wins_over_weak_for_the_same_pair():
    r = parse("""
Resources:
  Table:
    Type: AWS::DynamoDB::Table
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Environment:
        Variables:
          TABLE: !Ref Table
      Policies:
        - DynamoDBCrudPolicy:
            TableName: !Ref Table
""")
    assert ("lambda", "dynamodb") in strong(r)


def test_plumbing_never_becomes_a_node():
    r = parse("""
Resources:
  Role:
    Type: AWS::IAM::Role
  Perm:
    Type: AWS::Lambda::Permission
  Fn:
    Type: AWS::Serverless::Function
""")
    assert {n.service for n in r.graph.nodes} == {"lambda"}


def test_a_broken_rule_does_not_lose_the_other_edges():
    r = parse("""
Resources:
  Bucket:
    Type: AWS::S3::Bucket
    Properties:
      NotificationConfiguration:
        QueueConfigurations: "not a list"
  Q:
    Type: AWS::SQS::Queue
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Events:
        M:
          Type: SQS
          Properties:
            Queue: !GetAtt Q.Arn
""")
    assert ("sqs", "lambda") in edges(r)
