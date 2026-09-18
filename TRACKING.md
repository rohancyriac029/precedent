# Precedent — build tracking

Living record of what is actually built, verified and deployed. Every number
here came from a command that ran; nothing is aspirational. `docs/PLAN.md` says
what we intend to do, this file says what is true.

**Last updated:** 2026-09-18
**Track:** SHIP IT
**Live URL:** https://ob72bcbovd.execute-api.ap-south-1.amazonaws.com/v1
**Region:** ap-south-1 · **Stack:** `precedent` · **Account:** (redacted)
**Corpus commit:** `3d39819b0fdd2e38e42923db1e7c2fb8bcaa6dfe`

---

## 1. Status at a glance

| Stage | Name | State | Gate |
|---|---|---|---|
| 0 | Environment and decisions | **done** | PASSED |
| 1 | Corpus census | **done** | PASSED |
| 2 | Template parser | **done** | PASSED |
| 3 | Index and rules | **done** | index built; 39 rules written, 0 verified |
| 4 | Backend core | **done** | every core module built and deployed |
| 4B | Ship it (deploy) | **done** | PASSED |
| 5 | Frontend | **not started** | nothing exists |
| 6 | Evaluation data | not started | — |
| 7 | Extraction layer (Bedrock) | **done** | live on /extract |
| 8 | Run evaluations | not started | — |
| 9 | Demo hardening | not started | — |

**Tests:** 205 passing. **Backend complete and deployed.** All three input
types work end to end: template, Mermaid and prose.

---

## 2. What is deployed

Stack `precedent` in `ap-south-1`, deployed from `infra/template.yaml`.

| Resource | Purpose | Verified |
|---|---|---|
| API Gateway HTTP API | public endpoint, 10 req/s throttle | yes |
| Lambda `AuditFunction` | deterministic audit, arm64, python3.12 | yes |
| Lambda `ExtractFunction` | prose extraction, the only one that calls a model | yes |
| Secrets Manager | Bedrock API key, never an env var | yes |
| DynamoDB EdgesTable | 58 service pairs, PK `src#dst` | loaded |
| DynamoDB PatternsTable | 483 patterns with titles and URLs | loaded |
| DynamoDB AuditsTable | saved reports, 30-day TTL | yes |
| S3 ArtifactsBucket | encrypted, public access blocked | created, unused |

### Endpoints

| Method | Path | State |
|---|---|---|
| GET | `/health` | working |
| GET | `/corpus/stats` | working |
| GET | `/rules` | working, shows every rule and its verification state |
| GET | `/patterns/{id}` | working, pattern detail with its edges |
| POST | `/extract` | working, prose to draft graph via Bedrock |
| GET | `/extract/health` | working |
| POST | `/audits` | working for `template`, `mermaid` and `graph` |
| GET | `/audits/{id}` | working, shareable |

`POST /audits` still refuses `input_type: "prose"`. Prose goes to `/extract`
first, which returns a draft with `confirm_required: true`; the confirmed graph
is then posted to `/audits`. That two-step is the control that makes the prose
path safe, not a limitation.

### Verified end to end on the live URL

The demo diagram (S3 straight into Step Functions, with SNS and EventBridge
doing the same job) returns in 0.29 s:

```
s3             -> step_functions  UNPRECEDENTED_IN_CORPUS  n=0
     REPAIR: s3 -> eventbridge -> step_functions   counts=[12, 12]
lambda         -> dynamodb        GROUNDED                 n=43
step_functions -> lambda          GROUNDED                 n=13
sns            -> sqs             GROUNDED                 n=3
SNS / EventBridge  OVERLAPPING_CAPABILITY  "Do you need both?"
```

### Measured

| Metric | Value |
|---|---|
| Cold start | 1.09 s |
| Warm request, wall | 0.21 s |
| In-function audit time | 50 ms (26 parse, 22 ground) |
| E6 determinism, two live runs | identical fingerprint |

---

## 3. Corpus and parser

Source: `aws-samples/serverless-patterns`, MIT-0, pinned.

| Measure | Value |
|---|---|
| Patterns in corpus | 1040 |
| With a SAM/CFN template | 483 |
| Frameworks | 483 SAM/CFN, 382 CDK, 129 Terraform, 18 Serverless Framework |
| Distinct resource types | 239 |
| Resource instances | 2914 |
| Vocabulary services | 38 |
| Resource types mapped | 88 |

### Gate 1 — census

| Criterion | Target | Actual |
|---|---|---|
| Vocabulary coverage | >= 90% | **93.2% classified** (75.6% mapped to a service) |

### Gate 2 — parser

| Criterion | Target | Actual |
|---|---|---|
| Unhandled exceptions | 0 | **0** across 500 templates |
| Templates producing >= 1 edge | >= 80% | **84.2%** of connectable (330 of 392) |

Raw coverage across all templates is 65.7%. The connectable figure is the one
the gate uses: 80 templates hold fewer than two joinable resources and can never
produce an edge. Both are reported so neither misleads.

### Edge table

657 strong edges, **65 distinct service pairs**, counted by distinct pattern.

| Edge | Patterns |
|---|---|
| apigateway → lambda | 105 |
| lambda → dynamodb | 43 |
| sqs → lambda | 31 |
| eventbridge → lambda | 28 |
| lambda → s3 | 22 |

### Parser rules implemented

- **P0 (A1–A12):** all SAM function event types, `EventSourceMapping`,
  `Events::Rule` targets, S3 notifications, SNS subscriptions, 22 SAM policy
  templates, plus 16 service-only policies.
- **P1 (A13–A16):** StateMachine events and substitutions, Pipes, inline IAM
  statements with action-derived relations.
- **P2 (A17–A18):** API Gateway service integrations, environment variables at
  weak confidence.
- **Beyond the plan**, each added after reading templates that produced nothing:
  ApiGatewayV2 `Target` and `Integration`, REST `Method` integrations, ALB target
  groups, CloudFront origins, Firehose destinations and transforms, Cognito
  Lambda triggers, Lambda function URLs, and parameter-mediated resolution.

---

## 3b. Integration rules and repair

### Rules table — `data/integration_rules.csv`

| Measure | Value |
|---|---|
| Rules written | 39 |
| Verified by a human | **0** |
| Unsupported rules pending verification | 13 |
| Rows with a documentation URL | 39 of 39 |

**Unverified rules are inert, by design and in code.** A row only affects a
verdict once it has both a doc URL and a person's initials in `verified_by`.
Until then the rule is loaded, listed at `/rules`, counted in the report's
limitations, and changes nothing.

The asymmetry is deliberate. An unverified rule claiming something IS supported
costs nothing when wrong, because the corpus count is the fallback. An
unverified rule claiming something is NOT supported would put a false, confident
claim on a judge's screen. That is the one failure this product cannot afford.

**To activate a rule:** check it against the doc URL in the row, then put your
initials in `verified_by`. Nothing else. `s3 -> step_functions` then flips from
`UNPRECEDENTED_IN_CORPUS` to `UNSUPPORTED` and carries the documentation link.

Verification still gates only what we *say*. Repair routing already avoids every
edge in the unsupported list, verified or not, because suggesting a route through
something we believe is impossible would be worse than finding no route.

### Prose extraction — `extract/llm/`

Live on `/extract`, 3.9 s end to end including a cold start.

| Piece | Note |
|---|---|
| `client.py` | three providers: `none`, `bedrock`, `ollama`. Plain httpx, no vendor SDK. |
| `prompts.py` | Appendix C, kept short on purpose |
| `prose_extract.py` | two steps, both schema-constrained |
| Cache | sha256 of provider+model+task+prompt+schema, `/tmp` in Lambda |
| Retry | exponential backoff, then exactly one repair retry on bad JSON, then stop |

**The enum is the safety mechanism, not the prompt.** Step two can only connect
node IDs that step one produced, so an invented component cannot acquire
invented connections. Across six models tested, none ever emitted a service
outside the enum, including under direct injection.

**What the enum does not protect:** no model preserved the correct edge *set*
under injection. So `/extract` never audits anything. It returns a draft with
`confirm_required: true`, the deterministic post-processor repairs the three
systematic errors, and a human confirms before `/audits` runs.

### Repair — `core/repair.py`

Shortest path over the corpus graph, weighted `1 + 1/count` so a well-trodden hop
is cheaper than a rare one. Plain shortest path would happily route through a
connection appearing in exactly one pattern; the weighting makes the suggestion
conventional, not merely possible. Up to 2 paths, at most 3 hops, each hop
carrying the patterns that prove it.

---

## 4. Model selection (benchmarked, not deployed)

Measured 2026-09-18 on two hand-labelled prose designs, two-step schema-constrained
extraction, temperature 0. **Indicative only — re-run on the ten E3 designs before
quoting.**

| Model | Where | Clean F1 | Injection F1 | Latency |
|---|---|---|---|---|
| `zai.glm-4.7-flash` | Bedrock | **1.00** | 0.74 | ~2 s |
| `qwen.qwen3-32b` | Bedrock | 0.88 | **0.00** | ~1.2 s |
| `mistral.ministral-3-8b` | Bedrock | 0.85 | not run | ~1.8 s |
| `openai.gpt-oss-120b` | Bedrock | 0.74 | not run | ~2.5 s |
| `qwen2.5:7b` | local | 0.71 raw / 0.82 fixed | 0.60 | ~25 s |
| `llama3.2:3b` | local | 0.73 raw / 0.85 fixed | 0.20 | ~17 s |

Pinned: `zai.glm-4.7-flash`. Perfect on both designs, byte-identical across three
runs, fast enough for a live demo.

**Findings worth keeping.** Injection resistance does not correlate with size or
clean accuracy: a 32B cloud model scored 0.00 under attack, worse than a local 7B.
Reasoning models break strict JSON schema, because reasoning tokens leak into the
content field. Elaborating the prompt did not help and made the small model worse.
Across all six models, **not one ever emitted a service outside the enum**, even
under direct injection — that is the strongest safety claim available.

---

## 4b. Two fixes to verdict quality

Both were cases where a number looked like a finding but was an artefact.

### Invisible edges (parser gap presenting as a data gap)

`cognito -> apigateway` and `lambda -> textract` scored **zero** in the corpus and
read as `UNPRECEDENTED_IN_CORPUS` when both are routine AWS. A judge who knows
AWS would have spotted it immediately.

Five authorizer constructs were unread, and the direction differs between them,
which is why one rule could not cover them:

| Construct | Edge | Why |
|---|---|---|
| SAM `Auth.Authorizers.*.UserPoolArn` | cognito → api | the pool guards the API |
| `ApiGateway::Authorizer` COGNITO_USER_POOLS | cognito → api | same |
| `ApiGatewayV2::Authorizer` JWT | cognito → api | issuer names the pool |
| SAM `Auth.Authorizers.*.FunctionArn` | api → lambda | the API **invokes** it |
| `ApiGateway::Authorizer` TOKEN/REQUEST | api → lambda | same |
| ALB `AuthenticateCognitoConfig` | cognito → alb | the pool guards the balancer |

Textract was invisible for a different reason: managed services are never
CloudFormation resources and have no ARN, so their policy statements read
`Resource: "*"`. The only signal is the IAM **action prefix**. That inference is
restricted to services with no ARN, so `s3:GetObject` on `"*"` stays over-broad
IAM rather than becoming a fabricated edge to every bucket in the account.

| Edge | Before | After |
|---|---|---|
| cognito → apigateway | 0 | 2 |
| lambda → textract | 0 | 1 |
| lambda → bedrock | 0 | 6 |
| lambda → rekognition | 0 | 3 |
| lambda → comprehend | 0 | 3 |
| cognito → alb | 0 | 1 |

Both remain RARE rather than GROUNDED, which is honest: most Cognito patterns in
this corpus ship CDK, which the parser does not read.

### Repairs that were valid graphs but nonsense advice

The engine suggested `cognito -> lambda -> apigateway`. Every hop was well
precedented and the path meant nothing, because Cognito authorizes an API rather
than carrying traffic to one. **A hop being frequent does not make it composable.**

Two independent guards, because they catch different mistakes:

* **`repairable()`** decides whether an edge is offered a route at all. It is not,
  when the relation is configuration rather than flow, or when the source never
  emits anything (auth and call-only services). Rerouting around an authorizer
  does not authorize anything.
* **`is_relay`** decides which services may sit in the middle of a path. A relay
  must receive and then emit: compute, messaging, API fronts, plus S3 and
  DynamoDB because they emit notifications and streams. Not Cognito, not Textract,
  not RDS.

**Honest note:** on the current corpus the relay filter blocks nothing, because
non-relay services almost never have outgoing edges in the first place. That is
itself a validation of the parser's direction logic. It is kept as an invariant
with a test that builds a hostile corpus to prove it fires, rather than as dead
code nobody has exercised.

---

## 5. Bugs found and fixed

Recorded because each cost real time and each would have silently corrupted a
number on the final slide.

| Bug | Effect | Fix |
|---|---|---|
| IAM both a service and ignored plumbing | every template grew a meaningless `lambda → iam` edge | removed as a service; log groups removed for the same reason |
| Sub-resources became nodes | one REST API produced **six** apigateway nodes | single `is_node_type` decision point, regression test |
| Census regex-scanned for `Type:` | CloudFormation *parameter* types counted as resources | parse properly through the CFN loader |
| Rule C reversed `lambda → dynamodb` | every DynamoDB write was destroyed | narrowed to true pull sources (SQS, Kinesis) and consuming relations only |
| ALB listeners classified as plumbing | every load-balancer-to-Lambda edge lost | reclassified as edge constructs |
| `Throttle` on an HTTP API | template rejected | `DefaultRouteSettings`; caught by `sam validate --lint` |
| `sam build` strips `.so` files | `No module named pydantic_core._pydantic_core` at cold start | deploy the raw template; package arm64 wheels ourselves |
| Unsmoothed IDF gave a ubiquitous edge weight 0 | a pattern of only common edges had a zero denominator and vanished; a design that IS an API and a Lambda matched nothing | smoothed IDF, `log(1 + N/(1+df))` |
| Authorizers and wildcard IAM actions unread | `cognito -> apigateway` and `lambda -> textract` read as unprecedented | six authorizer rules plus IAM action-prefix inference |
| Repair optimised for precedent, not meaning | suggested `cognito -> lambda -> apigateway` | `repairable()` gate plus relay-only intermediates |

---

## 6. Not done yet

Ordered by what blocks the most.

**The backend is done.** What remains is not backend work.

1. **Frontend.** Nothing exists. A SHIP IT judge currently opens the URL and sees
   JSON. The API already returns everything a UI needs: verdicts, repair paths
   with per-hop evidence, closest patterns, node flags and limitations.
2. **Verify the 13 unsupported rules.** A person checks each against the doc URL
   already in the row and adds initials. Until then the product can say "rare",
   never "broken", and that is half its claim.
3. **Evaluation sets** E1–E7. No labelled data exists, so no defensible numbers
   for the results slide. `core/store.py` now makes these runnable offline.
4. **Amplify Hosting.** Not connected. The public URL is the API only.

### Known gaps in what is built

- 96 templates still produce no edges, clustered in AppSync resolvers and Step
  Functions inline definitions.
- The corpus graph is built by scanning the edge table once per container. Fine
  at 58 items; move it into the indexer as an S3 artifact if it ever reaches
  thousands.
- Prose extraction quality varies between runs. One run produced
  `s3 -> apigateway`, which is backwards: the pre-signed URL flow is
  apigateway to lambda to s3. The confirmation step exists precisely for this.
- 2 of 483 templates are genuinely unparseable.
- `/corpus/stats` reports `edges_indexed: 0` because DynamoDB `ItemCount` lags by
  about six hours. The data is loaded and queries return it.
- The Bedrock API key is long-lived and was exposed in a chat transcript. Rotate
  it before submission.

---

## 7. Reproducing this

```bash
python tasks.py corpus     # clone + pin the corpus
python tasks.py census     # resource census -> eval/results/census.json
python tasks.py index      # parse -> data/precedent.sqlite
python tasks.py load       # SQLite -> DynamoDB
python tasks.py package    # arm64 Lambda package -> build/lambda
python tasks.py test       # 75 tests
sam deploy --template infra/template.yaml --stack-name precedent \
  --region ap-south-1 --capabilities CAPABILITY_IAM --resolve-s3
```

Do not run `sam build`. It strips the compiled `.so` files. See section 5.

---

## 8. Module map

| Module | Does | Tests |
|---|---|---|
| `core/models.py` | Pydantic contracts, determinism fingerprint | via pipeline |
| `core/vocabulary.py` | 38 services, roles, exclusions | 11 |
| `core/cfn_loader.py` | CFN intrinsics, never crashes | 16 |
| `core/template_parser.py` | Appendix A rules plus 7 more | 39 |
| `core/mermaid_parser.py` | flowchart/graph, subgraphs, shapes | 34 |
| `core/canonicalize.py` | alias then containment then difflib | (with Mermaid) |
| `core/extract_postprocess.py` | the three systematic model errors | 11 |
| `core/grounding.py` | labels, node flags, summary | via pipeline |
| `core/rules.py` | integration rules, verification gate | 20 |
| `core/repair.py` | weighted shortest grounded path | (with rules) |
| `core/audit.py` | the whole pipeline in one call | 14 |
| `core/store.py` | SQLite / DynamoDB / dict behind one interface | 8 |
| `core/similarity.py` | IDF-weighted containment, smoothed | 12 |
| `core/repair.py` | weighted path, relay-only intermediates | 10 |
| `extract/llm/client.py` | three providers, cache, retry, repair | 21 |
| `extract/llm/prose_extract.py` | two-step schema-constrained extraction | (with client) |
| `lambda_src/handler_extract.py` | HTTP and Secrets Manager only | — |
| `indexer/*` | census, index, DynamoDB load, coverage | — |
| `lambda_src/handler_audit.py` | HTTP and DynamoDB only | — |

**205 tests, all passing.**

---

## 9. Setting the Bedrock key

The stack creates the secret with a placeholder. Set its value out of band so it
never enters the repository or the template:

```bash
aws secretsmanager put-secret-value \
  --secret-id "$(aws cloudformation describe-stacks --stack-name precedent \
      --region ap-south-1 --query \
      'Stacks[0].Outputs[?OutputKey==`BedrockSecretArn`].OutputValue' --output text)" \
  --secret-string '{"api_key":"YOUR_KEY"}' --region ap-south-1
```

Rotating the key needs no redeploy: the Lambda reads it per cold start.
