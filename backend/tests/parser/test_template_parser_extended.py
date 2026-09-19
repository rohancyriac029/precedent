"""Tests for the parser rules added after reading real corpus templates.

Each of these was written because a family of patterns in
aws-samples/serverless-patterns produced no edges. The docstrings name the
pattern that motivated the rule.
"""

from __future__ import annotations

from core.cfn_loader import loads
from core.template_parser import parse_template


def parse(text: str):
    return parse_template(loads(text))


def edges(result) -> set[tuple[str, str]]:
    return result.graph.service_edges()


def relations(result) -> set[str]:
    return {e.relation for e in result.graph.edges}


# --------------------------------------------------------------------------
# Parameter-mediated resolution (motivated by lambda-s3)
# --------------------------------------------------------------------------


def test_policy_and_resource_linked_through_a_shared_parameter():
    """lambda-s3 grants access by parameter, never by reference.

    The bucket is named by a parameter and the policy grants on the same
    parameter. Neither names the other, but the join is deterministic.
    """
    r = parse("""
Parameters:
  DestinationBucketName:
    Type: String
Resources:
  DestinationBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Ref DestinationBucketName
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - S3WritePolicy:
            BucketName: !Ref DestinationBucketName
""")
    assert ("lambda", "s3") in edges(r)


def test_shared_parameter_is_ignored_when_ambiguous():
    """Two resources named by the same parameter means no safe answer.

    A wrong edge inflates a precedent count, which is worse than a gap.
    """
    r = parse("""
Parameters:
  SharedName:
    Type: String
Resources:
  BucketA:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Ref SharedName
  BucketB:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Ref SharedName
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - S3WritePolicy:
            BucketName: !Ref SharedName
""")
    assert ("lambda", "s3") not in edges(r)


def test_direct_reference_still_wins_over_parameter_lookup():
    r = parse("""
Resources:
  Bucket:
    Type: AWS::S3::Bucket
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - S3ReadPolicy:
            BucketName: !Ref Bucket
""")
    assert ("lambda", "s3") in edges(r)


# --------------------------------------------------------------------------
# CloudFront (motivated by cloudfront-lambda-url-rust)
# --------------------------------------------------------------------------


def test_cloudfront_origin_reaches_through_nested_intrinsics():
    """The origin domain is Select of Split of GetAtt on a function URL."""
    r = parse("""
Resources:
  Fn:
    Type: AWS::Serverless::Function
  FnUrl:
    Type: AWS::Lambda::Url
    Properties:
      TargetFunctionArn: !GetAtt Fn.Arn
      AuthType: NONE
  Dist:
    Type: AWS::CloudFront::Distribution
    Properties:
      DistributionConfig:
        Origins:
          - Id: lambda
            DomainName: !Select [2, !Split ["/", !GetAtt FnUrl.FunctionUrl]]
""")
    assert ("cloudfront", "lambda_function_url") in edges(r)
    assert ("lambda_function_url", "lambda") in edges(r)


def test_cloudfront_origin_to_bucket():
    r = parse("""
Resources:
  Bucket:
    Type: AWS::S3::Bucket
  Dist:
    Type: AWS::CloudFront::Distribution
    Properties:
      DistributionConfig:
        Origins:
          - Id: s3
            DomainName: !GetAtt Bucket.DomainName
""")
    assert ("cloudfront", "s3") in edges(r)


# --------------------------------------------------------------------------
# Firehose (motivated by firehose-transformation-sam)
# --------------------------------------------------------------------------


def test_firehose_destination_and_transform():
    r = parse("""
Resources:
  DestinationBucket:
    Type: AWS::S3::Bucket
  TransformFn:
    Type: AWS::Serverless::Function
  Stream:
    Type: AWS::KinesisFirehose::DeliveryStream
    Properties:
      DeliveryStreamType: DirectPut
      ExtendedS3DestinationConfiguration:
        BucketARN: !GetAtt DestinationBucket.Arn
        ProcessingConfiguration:
          Enabled: true
          Processors:
            - Type: Lambda
              Parameters:
                - ParameterName: LambdaArn
                  ParameterValue: !GetAtt TransformFn.Arn
""")
    assert ("firehose", "s3") in edges(r)
    assert ("firehose", "lambda") in edges(r)


# --------------------------------------------------------------------------
# Cognito and Lambda URLs
# --------------------------------------------------------------------------


def test_cognito_lambda_trigger():
    r = parse("""
Resources:
  Fn:
    Type: AWS::Serverless::Function
  Pool:
    Type: AWS::Cognito::UserPool
    Properties:
      LambdaConfig:
        PreSignUp: !GetAtt Fn.Arn
""")
    assert ("cognito", "lambda") in edges(r)


def test_lambda_function_url_targets_its_function():
    r = parse("""
Resources:
  Fn:
    Type: AWS::Serverless::Function
  Url:
    Type: AWS::Lambda::Url
    Properties:
      TargetFunctionArn: !GetAtt Fn.Arn
      AuthType: NONE
""")
    assert ("lambda_function_url", "lambda") in edges(r)


# --------------------------------------------------------------------------
# A16: inline IAM statements
# --------------------------------------------------------------------------


def test_inline_iam_statement_creates_an_edge():
    r = parse("""
Resources:
  Secret:
    Type: AWS::SecretsManager::Secret
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - Statement:
            - Effect: Allow
              Action: secretsmanager:GetSecretValue
              Resource: !Ref Secret
""")
    assert ("lambda", "secrets_manager") in edges(r)


def test_iam_action_verb_picks_the_relation():
    write = parse("""
Resources:
  Table:
    Type: AWS::DynamoDB::Table
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - Statement:
            - Effect: Allow
              Action: [dynamodb:PutItem]
              Resource: !GetAtt Table.Arn
""")
    assert "writes" in relations(write)

    read = parse("""
Resources:
  Table:
    Type: AWS::DynamoDB::Table
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - Statement:
            - Effect: Allow
              Action: [dynamodb:GetItem]
              Resource: !GetAtt Table.Arn
""")
    assert "reads" in relations(read)


def test_iam_statement_inside_a_policy_document():
    r = parse("""
Resources:
  Q:
    Type: AWS::SQS::Queue
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - PolicyName: send
          PolicyDocument:
            Statement:
              - Effect: Allow
                Action: sqs:SendMessage
                Resource: !GetAtt Q.Arn
""")
    assert ("lambda", "sqs") in edges(r)


def test_iam_statement_to_a_non_node_resource_is_dropped():
    """A statement granting on an IAM role must not become an architecture edge."""
    r = parse("""
Resources:
  Role:
    Type: AWS::IAM::Role
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - Statement:
            - Effect: Allow
              Action: iam:PassRole
              Resource: !GetAtt Role.Arn
""")
    assert edges(r) == set()
