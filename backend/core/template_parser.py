"""SAM and CloudFormation template parser.

Appendix A of docs/PLAN.md. Stage 2, flagged there as the highest-risk work.

Turns a template into an ArchitectureGraph. Direction is data or control flow,
producer to consumer. An access edge points from the caller to the resource it
touches.

Two rules govern everything here:

* Never guess. A reference we cannot resolve inside this template becomes a
  warning and an `unknown` endpoint, never an invented edge. Precedent counts
  are only worth something if they are not padded with speculation.
* Never crash. A template that defeats one rule must still yield every edge the
  other rules can see.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from core.cfn_loader import LoadedTemplate, referenced_logical_ids
from core.models import ArchitectureGraph, Edge, Node, Relation
from core.vocabulary import Vocabulary, load as load_vocab

IMPLICIT_API_ID = "ServerlessRestApi"


# --------------------------------------------------------------------------
# SAM policy templates (rule A12)
#
# name -> (property holding the resource reference, relation)
# A policy template that names no resource still tells us the function talks to
# that service, so those are handled separately in SERVICE_ONLY_POLICIES.
# --------------------------------------------------------------------------

POLICY_TEMPLATES: dict[str, tuple[str, Relation]] = {
    "DynamoDBCrudPolicy": ("TableName", "writes"),
    "DynamoDBReadPolicy": ("TableName", "reads"),
    "DynamoDBWritePolicy": ("TableName", "writes"),
    "DynamoDBStreamReadPolicy": ("TableName", "reads"),
    "S3CrudPolicy": ("BucketName", "writes"),
    "S3ReadPolicy": ("BucketName", "reads"),
    "S3WritePolicy": ("BucketName", "writes"),
    "SQSSendMessagePolicy": ("QueueName", "sends"),
    "SQSPollerPolicy": ("QueueName", "reads"),
    "SNSPublishMessagePolicy": ("TopicName", "publishes"),
    "SNSCrudPolicy": ("TopicName", "publishes"),
    "EventBridgePutEventsPolicy": ("EventBusName", "publishes"),
    "StepFunctionsExecutionPolicy": ("StateMachineName", "starts_execution"),
    "LambdaInvokePolicy": ("FunctionName", "invokes"),
    "KinesisCrudPolicy": ("StreamName", "writes"),
    "KinesisStreamReadPolicy": ("StreamName", "reads"),
    "SSMParameterReadPolicy": ("ParameterName", "reads"),
    "SSMParameterWithSlashPrefixReadPolicy": ("ParameterName", "reads"),
    "FirehoseCrudPolicy": ("DeliveryStreamName", "writes"),
    "FirehoseWritePolicy": ("DeliveryStreamName", "writes"),
    "ElasticsearchHttpPostPolicy": ("DomainName", "writes"),
    "RekognitionLabelsPolicy": ("", "invokes"),
}

# Policies that grant access to a whole service with no specific resource.
# They still imply a real edge, so we create a synthetic node for the service.
SERVICE_ONLY_POLICIES: dict[str, str] = {
    "RekognitionDetectOnlyPolicy": "rekognition",
    "RekognitionLabelsPolicy": "rekognition",
    "RekognitionNoDataAccessPolicy": "rekognition",
    "RekognitionReadPolicy": "rekognition",
    "TextractDetectAnalyzePolicy": "textract",
    "TextractGetResultPolicy": "textract",
    "TextractPolicy": "textract",
    "ComprehendBasicAccessPolicy": "comprehend",
    "SESCrudPolicy": "ses",
    "SESBulkTemplatedCrudPolicy": "ses",
    "SESEmailTemplateCrudPolicy": "ses",
    "SESSendBouncePolicy": "ses",
    "PollyFullAccessPolicy": "polly",
    "TranslateFullAccess": "translate",
    "AWSSecretsManagerGetSecretValuePolicy": "secrets_manager",
    "BedrockInvokeModelPolicy": "bedrock",
}

# Function Events.Type -> (property naming the source, relation)
EVENT_SOURCES: dict[str, tuple[str, Relation]] = {
    "S3": ("Bucket", "triggers"),
    "SQS": ("Queue", "triggers"),
    "SNS": ("Topic", "triggers"),
    "DynamoDB": ("Stream", "triggers"),
    "Kinesis": ("Stream", "triggers"),
    "MSK": ("Stream", "triggers"),
    "DocumentDB": ("Cluster", "triggers"),
}

# API Gateway service-integration URIs embed the target service as a token:
#   arn:aws:apigateway:{region}:{service}:path/...
# and HTTP API integrations name it in IntegrationSubtype, for example
# EventBridge-PutEvents or StepFunctions-StartExecution.
APIGW_SERVICE_TOKENS: dict[str, str] = {
    "sqs": "sqs",
    "sns": "sns",
    "kinesis": "kinesis_streams",
    "firehose": "firehose",
    "dynamodb": "dynamodb",
    "states": "step_functions",
    "lambda": "lambda",
    "s3": "s3",
    "events": "eventbridge",
    "scheduler": "eventbridge_scheduler",
    "comprehend": "comprehend",
    "textract": "textract",
    "rekognition": "rekognition",
    "translate": "translate",
    "polly": "polly",
    "bedrock": "bedrock",
    "bedrock-runtime": "bedrock",
    "secretsmanager": "secrets_manager",
    "ssm": "ssm_parameter_store",
    "es": "opensearch",
}

INTEGRATION_SUBTYPE_SERVICES: dict[str, str] = {
    "EventBridge": "eventbridge",
    "SQS": "sqs",
    "SNS": "sns",
    "StepFunctions": "step_functions",
    "Kinesis": "kinesis_streams",
    "AppConfig": "ssm_parameter_store",
}

APIGW_URI_RE = re.compile(r"arn:aws[a-z-]*:apigateway:[^:]*:([a-z0-9-]+):")

# IAM action prefixes name an AWS service directly. A statement like
#   Action: textract:DetectDocumentText   Resource: "*"
# is the only signal that a function calls a managed service, because services
# such as Textract are never CloudFormation resources and have no ARN to
# reference. Without this the edge is invisible and the service reads as having
# no precedent when it is in fact routine.
IAM_SERVICE_PREFIXES: dict[str, str] = {
    "textract": "textract",
    "rekognition": "rekognition",
    "comprehend": "comprehend",
    "bedrock": "bedrock",
    "polly": "polly",
    "translate": "translate",
    "ses": "ses",
    "secretsmanager": "secrets_manager",
    "ssm": "ssm_parameter_store",
    "states": "step_functions",
    "dynamodb": "dynamodb",
    "s3": "s3",
    "sqs": "sqs",
    "sns": "sns",
    "events": "eventbridge",
    "scheduler": "eventbridge_scheduler",
    "kinesis": "kinesis_streams",
    "firehose": "firehose",
    "lambda": "lambda",
    "athena": "athena",
    "glue": "glue",
    "es": "opensearch",
    "aoss": "opensearch",
}

# Only these are worth inferring from a wildcard resource. The rest either have
# a real ARN to reference, or are plumbing we deliberately exclude.
IAM_WILDCARD_SERVICES = {
    "textract", "rekognition", "comprehend", "bedrock", "polly", "translate",
    "ses", "athena", "glue",
}

# Properties that name a resource. Used for parameter-mediated resolution:
# a policy often grants access by parameter, e.g.
#   Bucket:   {BucketName: !Ref DestinationBucketName}
#   Function: {Policies: [S3WritePolicy: {BucketName: !Ref DestinationBucketName}]}
# Neither side references the other, but both name the same parameter, so the
# link is a deterministic join rather than a guess.
NAME_PROPERTIES = (
    "BucketName",
    "TableName",
    "QueueName",
    "TopicName",
    "FunctionName",
    "StateMachineName",
    "StreamName",
    "DeliveryStreamName",
    "SecretName",
    "Name",
)

SCHEDULE_EVENTS = {"Schedule", "ScheduleV2"}
EVENTBRIDGE_EVENTS = {"EventBridgeRule", "CloudWatchEvent", "EventBridge"}
API_EVENTS = {"Api", "HttpApi"}


@dataclass
class ParseResult:
    graph: ArchitectureGraph
    warnings: list[str] = field(default_factory=list)


class TemplateParser:
    def __init__(self, template: LoadedTemplate, vocab: Optional[Vocabulary] = None):
        self.t = template
        self.v = vocab or load_vocab()
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.warnings: list[str] = list(template.warnings)
        self._synthetic: dict[str, str] = {}  # service -> node id

    # -- node construction --------------------------------------------------

    def _build_nodes(self) -> None:
        for logical_id, rtype, _props in self.t.iter_resources():
            # is_node_type excludes three things: plumbing we ignore, edge
            # constructs that are read rather than drawn, and sub-resources that
            # belong to a parent service. A REST API assembled from a RestApi,
            # a Deployment, a Stage and three Methods is one node, not six.
            if not self.v.is_node_type(rtype):
                continue
            service = self.v.service_for_resource_type(rtype)
            self.nodes[logical_id] = Node(
                id=logical_id,
                label=logical_id,
                service=service,
                canon_method="resource_type",
                source_ref=rtype,
            )

    def _synthetic_node(self, service: str) -> str:
        """A node for a service the template references but never declares.

        Managed services such as Textract are never CloudFormation resources;
        a function simply holds a policy for them. The edge is real, so the
        node has to exist.
        """
        if service in self._synthetic:
            return self._synthetic[service]
        node_id = f"Service::{service}"
        self.nodes[node_id] = Node(
            id=node_id,
            label=self.v.title(service),
            service=service,
            canon_method="resource_type",
            source_ref="implied by IAM policy",
        )
        self._synthetic[service] = node_id
        return node_id

    def _implicit_api(self) -> str:
        if IMPLICIT_API_ID not in self.nodes:
            self.nodes[IMPLICIT_API_ID] = Node(
                id=IMPLICIT_API_ID,
                label="Implicit API",
                service="apigateway",
                canon_method="resource_type",
                source_ref="SAM implicit API",
            )
        return IMPLICIT_API_ID

    # -- reference resolution ----------------------------------------------

    def _resolve(self, value: Any, context: str) -> Optional[str]:
        """Resolve an intrinsic to a single logical ID declared in this template.

        Returns None for a literal ARN, a parameter, or a cross-stack import.
        Those are legitimately unknowable from the template alone, and the plan
        is explicit that we warn rather than guess.
        """
        if value is None:
            return None
        refs = referenced_logical_ids(value)
        known = [r for r in refs if r in self.t.resources]
        if not known:
            # Both sides may name the same parameter instead of each other.
            via_param = self._resolve_via_parameter(value)
            if via_param:
                return via_param
            if isinstance(value, str) and value.startswith("arn:"):
                self.warnings.append(
                    f"{context}: points at a literal ARN outside this template; "
                    f"endpoint left unknown."
                )
            elif refs:
                self.warnings.append(
                    f"{context}: references {sorted(refs)!r}, which is not a resource "
                    f"in this template; endpoint left unknown."
                )
            return None
        if len(known) > 1:
            self.warnings.append(
                f"{context}: ambiguous, references {sorted(known)!r}; used the first."
            )
        return sorted(known)[0]

    def _node_for(self, logical_id: Optional[str]) -> Optional[str]:
        if logical_id is None:
            return None
        if logical_id in self.nodes:
            return logical_id
        rtype = self.t.resource_type(logical_id)
        if rtype and self.v.is_edge_construct(rtype):
            return None
        return None

    def _add(
        self,
        src: Optional[str],
        dst: Optional[str],
        relation: Relation,
        construct: str,
        confidence: str = "strong",
    ) -> None:
        if not src or not dst or src == dst:
            return
        if src not in self.nodes or dst not in self.nodes:
            return
        self.edges.append(
            Edge(
                src=src,
                dst=dst,
                relation=relation,
                confidence=confidence,  # type: ignore[arg-type]
                source_construct=construct,
            )
        )

    # -- rules --------------------------------------------------------------

    def _function_events(self, fn_id: str, props: dict[str, Any]) -> None:
        events = props.get("Events")
        if not isinstance(events, dict):
            return
        for event_name, spec in events.items():
            if not isinstance(spec, dict):
                continue
            etype = spec.get("Type")
            eprops = spec.get("Properties") if isinstance(spec.get("Properties"), dict) else {}
            ctx = f"{fn_id}.Events.{event_name}"

            if etype in EVENT_SOURCES:  # A1-A4
                prop, relation = EVENT_SOURCES[etype]
                src = self._resolve(eprops.get(prop), ctx)
                self._add(src, fn_id, relation, f"SAM.Function.Events.{etype}")

            elif etype in API_EVENTS:  # A5
                api_prop = "RestApiId" if etype == "Api" else "ApiId"
                api = self._resolve(eprops.get(api_prop), ctx) if eprops.get(api_prop) else None
                self._add(api or self._implicit_api(), fn_id, "invokes",
                          f"SAM.Function.Events.{etype}")

            elif etype in SCHEDULE_EVENTS:  # A6
                service = "eventbridge_scheduler" if etype == "ScheduleV2" else "eventbridge"
                self._add(self._synthetic_node(service), fn_id, "triggers",
                          f"SAM.Function.Events.{etype}")

            elif etype in EVENTBRIDGE_EVENTS:  # A7
                bus = self._resolve(eprops.get("EventBusName"), ctx) if eprops.get("EventBusName") else None
                self._add(bus or self._synthetic_node("eventbridge"), fn_id, "triggers",
                          f"SAM.Function.Events.{etype}")

    def _policies(self, fn_id: str, props: dict[str, Any]) -> None:
        """Rule A12, plus service-only policies."""
        policies = props.get("Policies")
        if policies is None:
            return
        for entry in policies if isinstance(policies, list) else [policies]:
            if isinstance(entry, str):
                if entry in SERVICE_ONLY_POLICIES:
                    self._add(fn_id, self._synthetic_node(SERVICE_ONLY_POLICIES[entry]),
                              "invokes", f"SAM.Policies.{entry}")
                continue
            if not isinstance(entry, dict):
                continue
            for name, params in entry.items():
                if name in SERVICE_ONLY_POLICIES:
                    self._add(fn_id, self._synthetic_node(SERVICE_ONLY_POLICIES[name]),
                              "invokes", f"SAM.Policies.{name}")
                    continue
                if name not in POLICY_TEMPLATES:
                    continue
                prop, relation = POLICY_TEMPLATES[name]
                if not prop or not isinstance(params, dict):
                    continue
                target = self._resolve(params.get(prop), f"{fn_id}.Policies.{name}")
                self._add(fn_id, target, relation, f"SAM.Policies.{name}")

    def _event_source_mapping(self, logical_id: str, props: dict[str, Any]) -> None:
        """Rule A8. The only signal in hand-written CloudFormation."""
        src = self._resolve(props.get("EventSourceArn"), f"{logical_id}.EventSourceArn")
        fn = self._resolve(props.get("FunctionName"), f"{logical_id}.FunctionName")
        self._add(src, fn, "triggers", "Lambda.EventSourceMapping")

    def _events_rule(self, logical_id: str, props: dict[str, Any]) -> None:
        """Rule A9. The rule itself is not a node; the bus is."""
        bus = self._resolve(props.get("EventBusName"), f"{logical_id}.EventBusName") \
            if props.get("EventBusName") else None
        source = bus or self._synthetic_node("eventbridge")
        targets = props.get("Targets")
        if not isinstance(targets, list):
            return
        for i, target in enumerate(targets):
            if not isinstance(target, dict):
                continue
            dst = self._resolve(target.get("Arn"), f"{logical_id}.Targets[{i}].Arn")
            self._add(source, dst, "triggers", "Events.Rule.Targets")

    def _bucket_notifications(self, bucket_id: str, props: dict[str, Any]) -> None:
        """Rule A10."""
        nc = props.get("NotificationConfiguration")
        if not isinstance(nc, dict):
            return
        for key, arn_prop in (
            ("LambdaConfigurations", "Function"),
            ("QueueConfigurations", "Queue"),
            ("TopicConfigurations", "Topic"),
        ):
            configs = nc.get(key)
            if not isinstance(configs, list):
                continue
            for i, cfg in enumerate(configs):
                if not isinstance(cfg, dict):
                    continue
                dst = self._resolve(cfg.get(arn_prop), f"{bucket_id}.{key}[{i}]")
                self._add(bucket_id, dst, "triggers", f"S3.NotificationConfiguration.{key}")
        if isinstance(nc.get("EventBridgeConfiguration"), dict):
            self._add(bucket_id, self._synthetic_node("eventbridge"), "triggers",
                      "S3.NotificationConfiguration.EventBridge")

    def _sns_subscription(self, logical_id: str, props: dict[str, Any]) -> None:
        """Rule A11."""
        topic = self._resolve(props.get("TopicArn"), f"{logical_id}.TopicArn")
        endpoint = self._resolve(props.get("Endpoint"), f"{logical_id}.Endpoint")
        self._add(topic, endpoint, "subscribes", "SNS.Subscription")

    def _state_machine(self, sm_id: str, props: dict[str, Any]) -> None:
        """Rules A13 and A14."""
        self._function_events(sm_id, props)  # Events block has the same shape
        self._policies(sm_id, props)
        subs = props.get("DefinitionSubstitutions")
        if isinstance(subs, dict):
            for key, value in subs.items():
                target = self._resolve(value, f"{sm_id}.DefinitionSubstitutions.{key}")
                self._add(sm_id, target, "invokes", "SAM.StateMachine.DefinitionSubstitutions")

    def _pipe(self, pipe_id: str, props: dict[str, Any]) -> None:
        """Rule A15. Source -> Pipe -> Target, collapsed through a pipe node."""
        node = self._synthetic_node("eventbridge")
        src = self._resolve(props.get("Source"), f"{pipe_id}.Source")
        dst = self._resolve(props.get("Target"), f"{pipe_id}.Target")
        self._add(src, node, "flows_to", "Pipes.Pipe.Source")
        self._add(node, dst, "flows_to", "Pipes.Pipe.Target")

    def _environment(self, fn_id: str, props: dict[str, Any]) -> None:
        """Rule A18, weak confidence: excluded from counts by default."""
        env = props.get("Environment")
        if not isinstance(env, dict):
            return
        variables = env.get("Variables")
        if not isinstance(variables, dict):
            return
        for key, value in variables.items():
            refs = referenced_logical_ids(value)
            for ref in sorted(refs):
                if ref in self.nodes and ref != fn_id:
                    self._add(fn_id, ref, "configured_with",
                              f"Environment.Variables.{key}", confidence="weak")

    # -- API Gateway integrations (rule A17 and friends) --------------------

    def _service_from_uri(self, value: Any) -> Optional[str]:
        """Pull the target service out of an API Gateway integration URI."""
        text = _flatten_to_text(value)
        if not text:
            return None
        m = APIGW_URI_RE.search(text)
        if not m:
            return None
        return APIGW_SERVICE_TOKENS.get(m.group(1))

    def _apigw_v2_api(self, api_id: str, props: dict[str, Any]) -> None:
        """A quick-create HTTP API names its target inline.

        AWS::ApiGatewayV2::Api.Target is a Sub ARN naming the Lambda, and it is
        the only edge signal in the apigw-http-api-lambda family of patterns.
        """
        target = props.get("Target")
        if target is None:
            return
        dst = self._resolve(target, f"{api_id}.Target")
        if dst:
            self._add(api_id, dst, "invokes", "ApiGatewayV2.Api.Target")
            return
        service = self._service_from_uri(target)
        if service:
            self._add(api_id, self._synthetic_node(service), "invokes",
                      "ApiGatewayV2.Api.Target")

    def _apigw_v2_integration(self, logical_id: str, props: dict[str, Any]) -> None:
        api = self._resolve(props.get("ApiId"), f"{logical_id}.ApiId")
        if not api:
            return
        subtype = props.get("IntegrationSubtype")
        if isinstance(subtype, str) and "-" in subtype:
            service = INTEGRATION_SUBTYPE_SERVICES.get(subtype.split("-", 1)[0])
            if service:
                self._add(api, self._synthetic_node(service), "invokes",
                          "ApiGatewayV2.Integration.Subtype")
                return
        uri = props.get("IntegrationUri")
        dst = self._resolve(uri, f"{logical_id}.IntegrationUri")
        if dst:
            self._add(api, dst, "invokes", "ApiGatewayV2.Integration.Uri")
            return
        service = self._service_from_uri(uri)
        if service:
            self._add(api, self._synthetic_node(service), "invokes",
                      "ApiGatewayV2.Integration.Uri")

    def _apigw_method(self, logical_id: str, props: dict[str, Any]) -> None:
        """REST API method wired straight to an AWS service."""
        api = self._resolve(props.get("RestApiId"), f"{logical_id}.RestApiId")
        if not api:
            return
        integration = props.get("Integration")
        if not isinstance(integration, dict):
            return
        uri = integration.get("Uri")
        dst = self._resolve(uri, f"{logical_id}.Integration.Uri")
        if dst:
            self._add(api, dst, "invokes", "ApiGateway.Method.Integration")
            return
        service = self._service_from_uri(uri)
        if service:
            self._add(api, self._synthetic_node(service), "invokes",
                      "ApiGateway.Method.Integration")

    # -- ALB ----------------------------------------------------------------

    def _collect_target_groups(self) -> None:
        """Target group members, collected before the listener pass runs."""
        self._target_groups: dict[str, list[str]] = {}
        for logical_id, rtype, props in self.t.iter_resources():
            if rtype != "AWS::ElasticLoadBalancingV2::TargetGroup":
                continue
            found: list[str] = []
            targets = props.get("Targets")
            for i, target in enumerate(targets if isinstance(targets, list) else []):
                if not isinstance(target, dict):
                    continue
                dst = self._resolve(target.get("Id"), f"{logical_id}.Targets[{i}].Id")
                if dst:
                    found.append(dst)
            self._target_groups[logical_id] = found

    def _alb_listener(self, logical_id: str, props: dict[str, Any]) -> None:
        """A listener joins a load balancer to a target group."""
        lb = self._resolve(props.get("LoadBalancerArn"), f"{logical_id}.LoadBalancerArn")
        if not lb:
            # A ListenerRule names its listener, not the balancer. Fall back to
            # the only load balancer in the template when there is exactly one.
            lbs = [n for n, node in self.nodes.items() if node.service == "alb"]
            if len(lbs) != 1:
                return
            lb = lbs[0]
        actions = props.get("DefaultActions") or props.get("Actions") or []
        for i, action in enumerate(actions if isinstance(actions, list) else []):
            if not isinstance(action, dict):
                continue
            tg = self._resolve(action.get("TargetGroupArn"), f"{logical_id}.Actions[{i}]")
            for dst in self._target_groups.get(tg or "", []):
                self._add(lb, dst, "invokes", "ElasticLoadBalancingV2.TargetGroup")

    # -- parameter-mediated resolution --------------------------------------

    def _build_parameter_index(self) -> None:
        """Map a parameter to the single resource named by it, when unambiguous.

        Only exact single-Ref name properties count. If two resources share the
        parameter the link is ambiguous and we record nothing, because a wrong
        edge is worse than a missing one.
        """
        index: dict[str, list[str]] = {}
        for logical_id, _rtype, props in self.t.iter_resources():
            for prop in NAME_PROPERTIES:
                value = props.get(prop)
                if isinstance(value, dict) and set(value) == {"Ref"}:
                    target = value["Ref"]
                    if isinstance(target, str) and target not in self.t.resources:
                        index.setdefault(target, []).append(logical_id)
        self._by_parameter = {
            param: ids[0] for param, ids in index.items() if len(ids) == 1
        }

    def _resolve_via_parameter(self, value: Any) -> Optional[str]:
        refs = referenced_logical_ids(value)
        hits = {self._by_parameter[r] for r in refs if r in self._by_parameter}
        if len(hits) == 1:
            return hits.pop()
        return None

    # -- additional constructs ---------------------------------------------

    def _cloudfront(self, dist_id: str, props: dict[str, Any]) -> None:
        """Distribution origins, including Lambda function URLs.

        The origin domain is often buried, e.g.
        Select 2 of Split "/" of GetAtt FnUrl.FunctionUrl, so the reference walk
        has to reach through the nested intrinsics.
        """
        config = props.get("DistributionConfig")
        if not isinstance(config, dict):
            return
        origins = config.get("Origins") or config.get("OriginGroups") or []
        for i, origin in enumerate(origins if isinstance(origins, list) else []):
            if not isinstance(origin, dict):
                continue
            dst = self._resolve(origin.get("DomainName"), f"{dist_id}.Origins[{i}]")
            self._add(dist_id, dst, "invokes", "CloudFront.Distribution.Origins")

    def _firehose(self, stream_id: str, props: dict[str, Any]) -> None:
        """Delivery stream destination and its optional Lambda transform."""
        for key in (
            "ExtendedS3DestinationConfiguration",
            "S3DestinationConfiguration",
            "RedshiftDestinationConfiguration",
            "ElasticsearchDestinationConfiguration",
            "AmazonopensearchserviceDestinationConfiguration",
        ):
            dest = props.get(key)
            if not isinstance(dest, dict):
                continue
            for arn_prop in ("BucketARN", "DomainARN", "ClusterJDBCURL"):
                dst = self._resolve(dest.get(arn_prop), f"{stream_id}.{key}.{arn_prop}")
                self._add(stream_id, dst, "writes", f"Firehose.{key}")
            processing = dest.get("ProcessingConfiguration")
            if isinstance(processing, dict):
                for j, proc in enumerate(processing.get("Processors") or []):
                    if not isinstance(proc, dict):
                        continue
                    fn = self._resolve(proc.get("Parameters"),
                                       f"{stream_id}.{key}.Processors[{j}]")
                    self._add(stream_id, fn, "invokes", "Firehose.ProcessingConfiguration")

    def _cognito_triggers(self, pool_id: str, props: dict[str, Any]) -> None:
        """A user pool invokes Lambda triggers on sign-up, auth and so on."""
        config = props.get("LambdaConfig")
        if not isinstance(config, dict):
            return
        for trigger, value in config.items():
            fn = self._resolve(value, f"{pool_id}.LambdaConfig.{trigger}")
            self._add(pool_id, fn, "triggers", f"Cognito.LambdaConfig.{trigger}")

    def _lambda_url(self, url_id: str, props: dict[str, Any]) -> None:
        fn = self._resolve(props.get("TargetFunctionArn"), f"{url_id}.TargetFunctionArn")
        self._add(url_id, fn, "invokes", "Lambda.Url.TargetFunctionArn")

    def _inline_policy_statements(self, principal: str, props: dict[str, Any]) -> None:
        """Rule A16: raw IAM statements inside a function's Policies block."""
        policies = props.get("Policies")
        if policies is None:
            return
        for entry in policies if isinstance(policies, list) else [policies]:
            if not isinstance(entry, dict):
                continue
            doc = entry.get("Statement") or (
                entry.get("PolicyDocument", {}).get("Statement")
                if isinstance(entry.get("PolicyDocument"), dict)
                else None
            )
            if not isinstance(doc, list):
                continue
            for stmt in doc:
                if not isinstance(stmt, dict):
                    continue
                relation = _relation_for_actions(stmt.get("Action"))
                for target in sorted(referenced_logical_ids(stmt.get("Resource"))):
                    if target in self.nodes and target != principal:
                        self._add(principal, target, relation, "IAM.Statement.Resource")

    # -- authorizers --------------------------------------------------------

    def _sam_auth(self, api_id: str, props: dict[str, Any]) -> None:
        """SAM `Auth` block on a Serverless Api or HttpApi.

        A Cognito authorizer is cognito -> api: the user pool guards the API.
        A Lambda authorizer is api -> lambda: the API invokes the function.
        The direction differs because the relationship differs, and getting it
        backwards would put a wrong edge in the precedent table.
        """
        auth = props.get("Auth")
        if not isinstance(auth, dict):
            return
        authorizers = auth.get("Authorizers")
        if not isinstance(authorizers, dict):
            return
        for name, spec in authorizers.items():
            if not isinstance(spec, dict):
                continue
            ctx = f"{api_id}.Auth.Authorizers.{name}"
            pool = self._resolve(spec.get("UserPoolArn"), ctx)
            if pool:
                self._add(pool, api_id, "configured_with", "SAM.Auth.UserPoolArn")
                continue
            fn = self._resolve(spec.get("FunctionArn"), ctx)
            if fn:
                self._add(api_id, fn, "invokes", "SAM.Auth.FunctionArn")
                continue
            # HTTP API JWT authorizer: the issuer URL names the user pool
            jwt = spec.get("JwtConfiguration")
            if isinstance(jwt, dict):
                issuer = self._resolve(jwt.get("issuer") or jwt.get("Issuer"), ctx)
                if issuer:
                    self._add(issuer, api_id, "configured_with", "SAM.Auth.Jwt")

    def _apigw_authorizer(self, logical_id: str, props: dict[str, Any]) -> None:
        """AWS::ApiGateway::Authorizer and AWS::ApiGatewayV2::Authorizer."""
        api = self._resolve(props.get("RestApiId") or props.get("ApiId"),
                            f"{logical_id}.ApiId")
        if not api:
            return
        kind = str(props.get("Type") or props.get("AuthorizerType") or "").upper()

        if "COGNITO" in kind or props.get("ProviderARNs"):
            for i, arn in enumerate(props.get("ProviderARNs") or []):
                pool = self._resolve(arn, f"{logical_id}.ProviderARNs[{i}]")
                self._add(pool, api, "configured_with", "ApiGateway.Authorizer.Cognito")
            return

        if kind == "JWT":
            jwt = props.get("JwtConfiguration")
            if isinstance(jwt, dict):
                issuer = self._resolve(jwt.get("Issuer"), f"{logical_id}.JwtConfiguration")
                if issuer:
                    self._add(issuer, api, "configured_with", "ApiGatewayV2.Authorizer.Jwt")
            return

        # TOKEN or REQUEST: a Lambda authorizer, invoked by the API.
        uri = props.get("AuthorizerUri")
        fn = self._resolve(uri, f"{logical_id}.AuthorizerUri")
        if fn:
            self._add(api, fn, "invokes", "ApiGateway.Authorizer.Lambda")

    def _alb_cognito(self, logical_id: str, props: dict[str, Any]) -> None:
        """A load balancer listener can authenticate through a user pool."""
        actions = props.get("DefaultActions") or props.get("Actions") or []
        lbs = [n for n, node in self.nodes.items() if node.service == "alb"]
        lb = self._resolve(props.get("LoadBalancerArn"), f"{logical_id}.LoadBalancerArn")
        if not lb and len(lbs) == 1:
            lb = lbs[0]
        if not lb:
            return
        for i, action in enumerate(actions if isinstance(actions, list) else []):
            if not isinstance(action, dict):
                continue
            cfg = action.get("AuthenticateCognitoConfig")
            if not isinstance(cfg, dict):
                continue
            pool = self._resolve(cfg.get("UserPoolArn"), f"{logical_id}.Actions[{i}]")
            self._add(pool, lb, "configured_with", "ElasticLoadBalancingV2.AuthenticateCognito")

    def _iam_service_actions(self, principal: str, props: dict[str, Any]) -> None:
        """Service-level IAM actions with a wildcard resource.

        Managed services such as Textract have no ARN to reference, so the only
        evidence a function calls one is the action prefix in its policy.
        """
        policies = props.get("Policies")
        if policies is None:
            return
        for entry in policies if isinstance(policies, list) else [policies]:
            if not isinstance(entry, dict):
                continue
            doc = entry.get("Statement") or (
                entry.get("PolicyDocument", {}).get("Statement")
                if isinstance(entry.get("PolicyDocument"), dict) else None
            )
            if not isinstance(doc, list):
                continue
            for stmt in doc:
                if not isinstance(stmt, dict):
                    continue
                if str(stmt.get("Effect", "Allow")).lower() != "allow":
                    continue
                resources = stmt.get("Resource")
                resources = resources if isinstance(resources, list) else [resources]
                # Only when the statement names no specific resource; an ARN is
                # handled by rule A16, which is more precise.
                if not any(r == "*" for r in resources if isinstance(r, str)):
                    continue
                actions = stmt.get("Action")
                actions = actions if isinstance(actions, list) else [actions]
                for action in actions:
                    if not isinstance(action, str) or ":" not in action:
                        continue
                    service = IAM_SERVICE_PREFIXES.get(action.split(":", 1)[0].lower())
                    if service and service in IAM_WILDCARD_SERVICES:
                        self._add(principal, self._synthetic_node(service), "invokes",
                                  "IAM.Statement.ServiceAction")

    # -- driver -------------------------------------------------------------

    def parse(self) -> ParseResult:
        self._build_nodes()
        self._build_parameter_index()
        self._collect_target_groups()

        for logical_id, rtype, props in self.t.iter_resources():
            try:
                if rtype in ("AWS::Serverless::Function", "AWS::Lambda::Function"):
                    self._function_events(logical_id, props)
                    self._policies(logical_id, props)
                    self._inline_policy_statements(logical_id, props)
                    self._iam_service_actions(logical_id, props)
                    self._environment(logical_id, props)
                elif rtype in ("AWS::Serverless::StateMachine", "AWS::StepFunctions::StateMachine"):
                    self._state_machine(logical_id, props)
                elif rtype == "AWS::Lambda::EventSourceMapping":
                    self._event_source_mapping(logical_id, props)
                elif rtype == "AWS::Events::Rule":
                    self._events_rule(logical_id, props)
                elif rtype == "AWS::S3::Bucket":
                    self._bucket_notifications(logical_id, props)
                elif rtype == "AWS::SNS::Subscription":
                    self._sns_subscription(logical_id, props)
                elif rtype == "AWS::Pipes::Pipe":
                    self._pipe(logical_id, props)
                elif rtype in ("AWS::Serverless::Api", "AWS::Serverless::HttpApi"):
                    self._sam_auth(logical_id, props)
                elif rtype in ("AWS::ApiGateway::Authorizer",
                               "AWS::ApiGatewayV2::Authorizer"):
                    self._apigw_authorizer(logical_id, props)
                elif rtype == "AWS::ApiGatewayV2::Api":
                    self._apigw_v2_api(logical_id, props)
                    self._sam_auth(logical_id, props)
                elif rtype == "AWS::ApiGatewayV2::Integration":
                    self._apigw_v2_integration(logical_id, props)
                elif rtype == "AWS::ApiGateway::Method":
                    self._apigw_method(logical_id, props)
                elif rtype in (
                    "AWS::ElasticLoadBalancingV2::Listener",
                    "AWS::ElasticLoadBalancingV2::ListenerRule",
                ):
                    self._alb_listener(logical_id, props)
                    self._alb_cognito(logical_id, props)
                elif rtype == "AWS::CloudFront::Distribution":
                    self._cloudfront(logical_id, props)
                elif rtype == "AWS::KinesisFirehose::DeliveryStream":
                    self._firehose(logical_id, props)
                elif rtype == "AWS::Cognito::UserPool":
                    self._cognito_triggers(logical_id, props)
                elif rtype == "AWS::Lambda::Url":
                    self._lambda_url(logical_id, props)
            except Exception as exc:  # never let one rule kill the parse
                self.warnings.append(
                    f"{logical_id} ({rtype}): rule failed, skipped. {type(exc).__name__}: {exc}"
                )

        graph = ArchitectureGraph(
            input_type="template",
            nodes=list(self.nodes.values()),
            edges=_dedupe(self.edges),
            warnings=self.warnings,
        )
        return ParseResult(graph=graph, warnings=self.warnings)


ACTION_RELATIONS: tuple[tuple[tuple[str, ...], Relation], ...] = (
    (("put", "write", "create", "update", "delete", "send"), "writes"),
    (("publish",), "publishes"),
    (("start", "execute"), "starts_execution"),
    (("invoke",), "invokes"),
    (("get", "read", "list", "query", "scan", "describe", "receive"), "reads"),
)


def _relation_for_actions(action: Any) -> Relation:
    """Derive a relation from IAM action verbs, defaulting to the safer read."""
    actions = action if isinstance(action, list) else [action]
    text = " ".join(a.lower() for a in actions if isinstance(a, str))
    if not text:
        return "reads"
    for verbs, relation in ACTION_RELATIONS:
        for verb in verbs:
            if f":{verb}" in text:
                return relation
    return "reads"


def _flatten_to_text(value: Any) -> str:
    """Render an intrinsic expression to searchable text.

    An integration URI is usually a Join or a Sub whose literal parts carry the
    service token we need, so the structure has to be flattened first.
    """
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for inner in v.values():
                walk(inner)
        elif isinstance(v, list):
            for item in v:
                walk(item)

    walk(value)
    return " ".join(out)


def _dedupe(edges: Iterable[Edge]) -> list[Edge]:
    """Keep one edge per (src, dst), preferring strong confidence."""
    best: dict[tuple[str, str], Edge] = {}
    for e in edges:
        key = e.pair()
        prev = best.get(key)
        if prev is None or (prev.confidence == "weak" and e.confidence == "strong"):
            best[key] = e
    return list(best.values())


def parse_template(template: LoadedTemplate, vocab: Optional[Vocabulary] = None) -> ParseResult:
    return TemplateParser(template, vocab).parse()
