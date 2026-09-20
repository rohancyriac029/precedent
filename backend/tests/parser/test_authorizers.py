"""Authorizer and service-level IAM rules.

Both were added because real edges were invisible: `cognito -> apigateway` and
`lambda -> textract` scored zero in the corpus and read as UNPRECEDENTED when
they are in fact routine. A parser gap presenting as a data gap is the worst
kind, because the number looks like a finding.
"""

from __future__ import annotations

from core.cfn_loader import loads
from core.template_parser import parse_template


def parse(text: str):
    return parse_template(loads(text))


def edges(r) -> set[tuple[str, str]]:
    return r.graph.service_edges()


def relation_for(r, src, dst):
    g = r.graph
    for e in g.edges:
        if (g.service_of(e.src), g.service_of(e.dst)) == (src, dst):
            return e.relation
    return None


# ---------------------------------------------------------------------------
# Cognito authorizers
# ---------------------------------------------------------------------------


def test_sam_api_cognito_authorizer():
    """The shape used by amplify_cognito_apigateway_lambda_envvariables."""
    r = parse("""
Transform: AWS::Serverless-2016-10-31
Resources:
  UserPool:
    Type: AWS::Cognito::UserPool
  Api:
    Type: AWS::Serverless::Api
    Properties:
      StageName: prod
      Auth:
        DefaultAuthorizer: MyCognitoAuthorizer
        Authorizers:
          MyCognitoAuthorizer:
            UserPoolArn: !GetAtt UserPool.Arn
""")
    assert ("cognito", "apigateway") in edges(r)
    assert relation_for(r, "cognito", "apigateway") == "configured_with"


def test_raw_cfn_cognito_authorizer():
    """The shape used by cognito-restapi."""
    r = parse("""
Resources:
  UserPool:
    Type: AWS::Cognito::UserPool
  Api:
    Type: AWS::ApiGateway::RestApi
  Auth:
    Type: AWS::ApiGateway::Authorizer
    Properties:
      RestApiId: !Ref Api
      Type: COGNITO_USER_POOLS
      IdentitySource: method.request.header.authorizationToken
      ProviderARNs:
        - !GetAtt UserPool.Arn
""")
    assert ("cognito", "apigateway") in edges(r)


def test_authorizer_itself_is_never_a_node():
    r = parse("""
Resources:
  UserPool:
    Type: AWS::Cognito::UserPool
  Api:
    Type: AWS::ApiGateway::RestApi
  Auth:
    Type: AWS::ApiGateway::Authorizer
    Properties:
      RestApiId: !Ref Api
      Type: COGNITO_USER_POOLS
      ProviderARNs: [!GetAtt UserPool.Arn]
""")
    assert "Auth" not in {n.id for n in r.graph.nodes}


def test_lambda_authorizer_points_the_other_way():
    """Direction differs because the relationship differs.

    A user pool guards the API, so cognito -> api. A Lambda authorizer is
    invoked BY the API, so api -> lambda. Getting this backwards would put a
    wrong edge into the precedent table.
    """
    r = parse("""
Resources:
  Api:
    Type: AWS::ApiGateway::RestApi
  Fn:
    Type: AWS::Serverless::Function
  Auth:
    Type: AWS::ApiGateway::Authorizer
    Properties:
      RestApiId: !Ref Api
      Type: TOKEN
      AuthorizerUri: !GetAtt Fn.Arn
""")
    assert ("apigateway", "lambda") in edges(r)
    assert ("lambda", "apigateway") not in edges(r)


def test_sam_lambda_authorizer_via_function_arn():
    r = parse("""
Transform: AWS::Serverless-2016-10-31
Resources:
  Fn:
    Type: AWS::Serverless::Function
  Api:
    Type: AWS::Serverless::Api
    Properties:
      StageName: prod
      Auth:
        Authorizers:
          CustomAuthorizer:
            FunctionArn: !GetAtt Fn.Arn
""")
    assert ("apigateway", "lambda") in edges(r)


def test_alb_cognito_authentication():
    r = parse("""
Resources:
  UserPool:
    Type: AWS::Cognito::UserPool
  LB:
    Type: AWS::ElasticLoadBalancingV2::LoadBalancer
  Listener:
    Type: AWS::ElasticLoadBalancingV2::Listener
    Properties:
      LoadBalancerArn: !Ref LB
      DefaultActions:
        - Type: authenticate-cognito
          AuthenticateCognitoConfig:
            UserPoolArn: !GetAtt UserPool.Arn
""")
    assert ("cognito", "alb") in edges(r)


# ---------------------------------------------------------------------------
# Service-level IAM actions
# ---------------------------------------------------------------------------


def test_textract_action_with_wildcard_resource():
    """Textract is never a CFN resource, so the action prefix is the only signal."""
    r = parse("""
Resources:
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - Statement:
            - Effect: Allow
              Action: textract:DetectDocumentText
              Resource: "*"
""")
    assert ("lambda", "textract") in edges(r)


def test_multiple_service_actions_each_produce_an_edge():
    r = parse("""
Resources:
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - Statement:
            - Effect: Allow
              Action:
                - rekognition:DetectLabels
                - comprehend:DetectSentiment
              Resource: "*"
""")
    assert ("lambda", "rekognition") in edges(r)
    assert ("lambda", "comprehend") in edges(r)


def test_deny_statements_are_ignored():
    r = parse("""
Resources:
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - Statement:
            - Effect: Deny
              Action: textract:DetectDocumentText
              Resource: "*"
""")
    assert ("lambda", "textract") not in edges(r)


def test_wildcard_rule_does_not_fire_for_services_with_a_real_arn():
    """s3:* on "*" is over-broad IAM, not evidence of an architectural edge.

    Restricting the wildcard inference to managed services that have no ARN
    keeps it from inventing an edge to every bucket in the account.
    """
    r = parse("""
Resources:
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - Statement:
            - Effect: Allow
              Action: s3:GetObject
              Resource: "*"
""")
    assert ("lambda", "s3") not in edges(r)


def test_specific_arn_still_uses_the_precise_rule():
    r = parse("""
Resources:
  Bucket:
    Type: AWS::S3::Bucket
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Policies:
        - Statement:
            - Effect: Allow
              Action: s3:GetObject
              Resource: !GetAtt Bucket.Arn
""")
    assert ("lambda", "s3") in edges(r)
