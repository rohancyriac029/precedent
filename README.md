# Precedent

**Audit an AWS architecture against what real, deployable AWS patterns actually do.**

Live app: **https://main.d1d8fvj75prbll.amplifyapp.com**
API: `https://ob72bcbovd.execute-api.ap-south-1.amazonaws.com/v1`

Paste a SAM or CloudFormation template, a Mermaid diagram, or a plain-English
description. Precedent breaks it into service-to-service connections and checks
each one against the
[AWS Serverless Patterns Collection](https://github.com/aws-samples/serverless-patterns),
pinned at one commit. Each connection gets a verdict you can reproduce, the real
patterns behind that verdict, and, where a connection is weak, the cheapest route
around it that real patterns do use.

The verdicts are computed by code, never by a model. A model is used in only two
places, both optional: turning prose into a draft graph that you confirm, and
writing a review of, or answering questions about, a finished audit.

For a guided walkthrough with inputs to paste, see [DEMO.md](DEMO.md).
To set up a development machine, see [CONTRIBUTING.md](CONTRIBUTING.md).
The full design and stage checklist is in [docs/PLAN.md](docs/PLAN.md).

---

## Contents

1. [The problem](#1-the-problem)
2. [What it does](#2-what-it-does)
3. [How an audit works, end to end](#3-how-an-audit-works-end-to-end)
4. [The deterministic core](#4-the-deterministic-core)
5. [The rule engine](#5-the-rule-engine)
6. [Where a model is used, and how it is contained](#6-where-a-model-is-used-and-how-it-is-contained)
7. [The deterministic policy](#7-the-deterministic-policy)
8. [Deployed architecture on AWS](#8-deployed-architecture-on-aws)
9. [API reference](#9-api-reference)
10. [Frontend](#10-frontend)
11. [Repository layout](#11-repository-layout)
12. [Running, testing, deploying](#12-running-testing-deploying)
13. [Limitations and what is not done](#13-limitations-and-what-is-not-done)

---

## 1. The problem

Architecture diagrams, many of them now drafted by chat assistants, are full of
connections that look plausible and are not how anyone builds on AWS. An S3
bucket "directly starting" a Step Functions workflow is a typical example: every
box is a real service, the arrow reads naturally, and published patterns go
through EventBridge or a Lambda function instead.

A best-practice checklist does not catch this, and asking another model is
asking the same kind of system that produced the design. Precedent answers a
narrower question that can be checked: **for each connection, how many real,
deployable patterns make it, and which ones?**

## 2. What it does

| Capability | What you get |
|---|---|
| **Three input types** | SAM/CloudFormation templates and Mermaid flowcharts are parsed deterministically. Prose is drafted into a graph by a model, and you confirm or edit it before anything is audited. |
| **A verdict on every connection** | `GROUNDED`, `RARE`, `UNPRECEDENTED_IN_CORPUS`, `UNSUPPORTED` or `UNKNOWN_SERVICE`, with the pattern count and up to five evidence patterns, each linked to its source. |
| **Routes around weak connections** | For weak connections, up to two routes where every hop is itself precedented, for example S3 → EventBridge → Step Functions. |
| **Component questions** | Components that connect to nothing, and two services of the same kind that share a producer and a consumer (a possible redundancy, reported as a question, not a verdict). |
| **Closest real patterns** | The published patterns your design most resembles, weighted so that rare shared connections count for more than common ones. |
| **Review** | A short written review of the finished audit, citing only patterns the audit already cites. |
| **Ask about this audit** | Questions answered only from this audit and the READMEs of the patterns it cites. |
| **Share, export, print** | Every audit is saved for 30 days behind an opaque ID (`?audit=<id>`), downloadable as JSON, and printable as a drawing sheet. |

## 3. How an audit works, end to end

```
                         ┌───────────── deterministic, never calls a model ─────────────┐
 template ─► cfn_loader ─► template_parser ─┐
                                            ├─► graph ─► ground ─► rules ─► repair ─► closest ─► report
 mermaid  ─► mermaid_parser + canonicalize ─┘                                                    │
                                                                                                 ▼
 prose ─► sanitize ─► model draft (2 steps) ─► post-process ─► YOU CONFIRM ─► graph    saved by audit id
          └────────── extract function, optional ──────────┘                                     │
                                                                                                 ▼
                                               review / ask ◄── BM25 over cited patterns ◄── report by id
                                               └──── extract function, optional, drafts only ────┘
```

1. **Parse.** The input becomes an `ArchitectureGraph`: nodes with a canonical
   service id, and edges with a relation and a confidence (`strong` or `weak`).
2. **Ground.** Each distinct service-level edge `(src_service, dst_service)` is
   looked up in the corpus edge table: a count of distinct patterns, and evidence.
3. **Rules.** A human-verified rules table can mark a direct integration as
   `UNSUPPORTED`.
4. **Repair.** Weak edges get the cheapest precedented route around them.
5. **Closest patterns.** The whole design is compared with every pattern.
6. **Report.** A deterministic summary sentence, per-component flags,
   limitations, and a fingerprint that must not change between identical runs.

The service-level edge is the unit everything agrees on: the parsers emit it,
the labels are computed on it, and the evaluations score it.

## 4. The deterministic core

Everything in `backend/core/` is pure and deterministic. It may read `data/`
and never imports `extract/`.

### 4.1 The corpus index (offline)

`indexer/build_index.py` parses every SAM/CloudFormation template in the pinned
corpus with the same parser users get, and writes:

| Output | Contents | Used by |
|---|---|---|
| `data/precedent.sqlite` | pattern rows, edge rows, meta | local API, source for DynamoDB |
| `data/pattern_index.json` | pattern → its service edges, title, URL | closest patterns, bundled in the Lambda |
| `data/readme_chunks.json` | descriptive README passages per pattern | review and Q&A retrieval, bundled |

`indexer/load_ddb.py` then pushes the edge and pattern tables to DynamoDB.

**The count that matters is distinct patterns, not edge rows.** A pattern that
wires three Lambda functions to one table is one piece of evidence for
`lambda → dynamodb`, not three. Only `strong` edges are counted.

The pinned commit is in `backend/data/corpus_commit.txt`, and every report
carries it. Current corpus statistics are served live at `/corpus/stats`.

### 4.2 Vocabulary and canonicalization

`data/vocabulary.yaml` is the single source of truth for 38 services. For each
one it gives:

- the CloudFormation resource types that map to it
- its **roles**, which the post-processor and the repair step key off:

  | Role | Services | Meaning |
  |---|---|---|
  | `call_only` | Bedrock, Comprehend, Polly, Rekognition, Secrets Manager, SES, SSM Parameter Store, Textract, Translate | answers calls, never originates a connection |
  | `auth` | Cognito | protects an API surface; traffic does not flow out of it |
  | `api_front` | ALB, API Gateway, AppSync, CloudFront, Lambda function URL | where requests enter |
  | `pull_source` | SQS, Kinesis Data Streams | consumers poll it, so data flows *from* it |
  | `relay` | 16 services | can receive and then emit, so it may sit in the middle of a repair route |

- **capability classes** for the overlap check (messaging, database, API front)
- **SDK-capable** services (Lambda, Step Functions, ECS, Fargate), which can call
  any AWS API
- resource types to **ignore** (IAM, logs, networking, packaging glue) and
  resource types that are **edge constructs** (an `EventSourceMapping` is how a
  connection is declared, not a service)

Mermaid labels are free text, so `core/canonicalize.py` maps them to a service
in three passes: an exact alias from `data/aliases.csv`, then a whole-word alias
inside the label ("Document Bucket (S3)"), then fuzzy matching with `difflib` at
a 0.86 threshold. Anything below that becomes `unknown`, never a guess. There
are no embeddings anywhere in the runtime.

### 4.3 Template parser

`core/template_parser.py` turns a template into a graph through explicit, tested
rules, listed in full in Appendix A of the plan. Direction is always data or
control flow, producer to consumer. The main families:

| Rules | What they read |
|---|---|
| A1–A7 | SAM function `Events`: S3, SQS, SNS, DynamoDB/Kinesis streams, Api/HttpApi, Schedule, EventBridge rules |
| A8–A11 | `EventSourceMapping`, `Events::Rule` targets, bucket notifications, SNS subscriptions |
| A12, A16 | SAM policy templates and inline IAM statements that name a resource |
| A13–A15 | Step Functions events and definition substitutions, EventBridge Pipes |
| A17 | API Gateway service integrations (the URI names the target service) |
| A18 | environment variables that reference a resource, as a **weak** edge, excluded from counts |
| A19 | IoT Core topic rule actions and SAM `IoTRule` events |
| extensions | Cognito and Lambda authorizers, ALB listeners and Cognito auth, CloudFront origins, Firehose destinations, Cognito triggers, Lambda URLs |

Two principles govern the parser:

- **Never guess.** A reference that cannot be resolved inside the template (a
  literal ARN, a cross-stack import) produces a warning and no edge. The one
  exception is deterministic: when a policy and a resource name the same
  template parameter, and only one resource does, they are joined.
- **Never crash.** A rule that fails on one resource is skipped with a warning,
  and every other rule still runs.

### 4.4 Grounding and labels

`core/grounding.py` computes the label with a pure function:

```python
def label_edge(src, dst, facts, vocab, rule):
    if not vocab.has(src) or not vocab.has(dst): return "UNKNOWN_SERVICE"
    if rule is not None:                         return "UNSUPPORTED"
    if facts.count >= 3:                         return "GROUNDED"
    if facts.count >= 1:                         return "RARE"
    return "UNPRECEDENTED_IN_CORPUS"
```

The honesty rule is built into the name: **absence from the corpus is not
evidence of impossibility.** The label is "no precedent in this corpus", never
"novel" and never "unsupported", unless a verified, documented rule says so.

The node checks flag `ORPHAN` (connected to nothing) and
`OVERLAPPING_CAPABILITY` (two services in the same capability class that share
an upstream and a downstream). Overlap is phrased as a question: "Do you need
both?"

### 4.5 Repair: routes around a weak connection

`core/repair.py` builds a directed graph of every service pair the corpus has
seen, leaving out any pair a verified rule marks as unsupported, and searches it:

```
weight(hop) = 1 + 1 / count        # every hop costs at least 1, common hops cost less
return up to 2 simple paths, 2 to 3 hops, cheapest first
```

- **The weighting** means a well-trodden route beats a technically possible one
  through a hop seen once.
- **Intermediates must be relays.** Without this, a Cognito edge was once
  "repaired" through `cognito → lambda → apigateway`: two individually common
  hops forming a nonsense route. Only services that can receive and then emit
  may sit in the middle.
- **The `repairable()` gate** skips edges where a route makes no sense: the edge
  is already `GROUNDED`, it is configuration rather than flow (Cognito
  protecting an API), or its source never emits (auth and call-only services).
- **A direct edge is never its own alternative.** A `RARE` edge is in the
  corpus graph, and returning it as a one-hop route would be circular.
- **If there is no path, it says so.** Inventing a route it cannot evidence
  would be the same failure the product exists to catch.

### 4.6 Closest patterns

`core/similarity.py` ranks patterns by IDF-weighted containment:

```
idf(e)            = log(1 + N / (1 + df(e)))                     # smoothed, never zero
containment(A, P) = Σ idf(e) for e in A∩P  /  Σ idf(e) for e in P
```

Containment rather than Jaccard, because patterns are small and designs are
large. The question is how much of this pattern your design already contains.
IDF because sharing `apigateway → lambda`, which is in more than a hundred
patterns, says almost nothing, while sharing a rare edge is real evidence. Ties
break on pattern ID, so the ordering is total.

### 4.7 The report and its fingerprint

`core/audit.py` runs the whole pipeline. `AuditReport.deterministic_fingerprint()`
hashes the canonical JSON of the report, excluding only the audit ID, the
timings and the narrative. The same input over the same corpus commit must
produce the same fingerprint, and the UI shows it on every audit.

## 5. The rule engine

`data/integration_rules.csv` records what AWS documents as a supported, or
unsupported, direct integration:

```
rule_id,src_service,dst_service,relation_scope,direct_supported,doc_url,verified_by,note
R001,s3,lambda,event_notification,true,https://docs.aws.amazon.com/...,,S3 event notifications support Lambda...
```

A precedent count can only ever say something is rare. `UNSUPPORTED` says it
will not work, a much stronger claim, so it carries a much stronger evidence
requirement. `core/rules.py` makes that requirement structural:

1. **A rule is inert until verified.** It affects a verdict only when it has
   both a documentation URL and a person's initials in `verified_by`. Unverified
   rows are loaded, counted, listed at `/rules`, and reported in each audit's
   notes, but they never change a label.
2. **The asymmetry is deliberate.** A wrong "supported" rule costs nothing,
   because the fallback is the corpus count. A wrong "not supported" rule would
   put a false claim on screen.
3. **SDK-capable sources are exempt.** A Lambda function can call any AWS API
   through the SDK, so a rule saying a direct *event* integration is unsupported
   does not apply to it.
4. **Verified unsupported pairs are removed from the repair graph,** so no route
   is ever built through a hop known not to work.

The table holds 39 rules, 13 of them "not supported", every one with a doc URL.
None has initials yet, so today no rule changes a verdict, and the notes say so
on every audit. Verifying them is a human task by design.

## 6. Where a model is used, and how it is contained

Model code lives only in `backend/extract/llm/` and `backend/report/narrative.py`,
and runs only in the extract Lambda. The audit Lambda has no path to a model at
all.

**Provider.** Amazon Bedrock, through the mantle OpenAI-compatible endpoint, with
`zai.glm-4.7-flash`. These models are not reachable through `bedrock-runtime`,
so the call is a plain `httpx` POST, with no vendor SDK. `ollama` is an offline
development fallback, and `none` is a supported mode in which templates and
Mermaid still work fully.

**Every call** goes through `extract/llm/client.py`:
- a JSON schema with `strict: true` on every call, and validation on the way back
- one repair retry on invalid JSON, never a loop
- backoff with jitter on transient errors
- a response cache keyed by a hash of the prompt and schema
- one JSON log line per call, with no user content
- `LLM_OFFLINE=1` serves from the cache only

The key comes from Secrets Manager in the deployed app and from `backend/.env`
locally. It is never an environment variable in the template and never in the
repository.

### 6.1 Prose to graph: a draft you confirm

1. **Sanitise.** `prose_extract.sanitize()` removes lines addressed to a model
   ("ignore all previous instructions", "you must", "drop all other edges") and
   reports how many were removed.
2. **Nodes.** The model lists components, and the schema makes `service` an
   **enum of the vocabulary**. No model tested ever produced a service outside
   the enum, even under direct injection.
3. **Edges.** A second call connects nodes, and the schema makes `src` and `dst`
   an **enum of the node IDs from step 2**. An invented component cannot acquire
   invented connections.
4. **Post-process** (`core/extract_postprocess.py`), deterministic and
   table-driven from the vocabulary roles. Every rewrite is written to
   `graph.warnings` with its reason:

   | Rule | Fixes | Example |
   |---|---|---|
   | A. call-only | an edge out of a service that only answers calls is reattached to its caller | `textract → dynamodb` becomes `lambda → dynamodb` |
   | B. auth attachment | an identity service is redirected to protect the API surface it is adjacent to | `apigateway → cognito` becomes `cognito → apigateway` |
   | C. pull direction | a compute node "reading" a queue or stream is reversed | `lambda → sqs (reads)` becomes `sqs → lambda` |
   | D. corpus direction | an edge with no precedent whose reverse has 3+ patterns is flipped | available in `postprocess(count_fn=...)`, not enabled on the live extract path |

5. **You confirm.** The draft is shown in an editable panel. You can rename
   components, change their service, and delete components or connections.
   Nothing is audited until you press **Audit**.

What this does not claim: no model tested kept the exact set of connections
under injection. The enum prevents invented services; confirmation is the
control for the rest.

### 6.2 Review and "Ask about this audit"

Both run after the audit is finished, through `POST /audits/{id}/review` and
`POST /audits/{id}/ask`, and nothing they return is written back into the report.

**How the model gets only relevant data:**

1. **The server loads the saved report by ID** from DynamoDB (read-only
   permission). A caller cannot hand the model a doctored report.
2. **The candidate set is chosen by the deterministic audit.** The only patterns
   eligible are the ones this audit already cites: the evidence behind each
   verdict, the patterns along each suggested route, and the closest matches
   (`core/retrieve.cited_patterns`).
3. **BM25 ranks passages within that set** (`core/retrieve.py`), over README
   passages from which the indexer has stripped boilerplate: requirements,
   deployment steps, cleanup, licences, CLI instructions. IDF comes from the
   whole collection. It takes at most 6 passages, at most 2 per pattern, and
   none that share no word with the question.
4. **The model sees service-level facts only.** It gets labels, counts, the
   count for each hop of a route, and the closest patterns. Node names, logical
   IDs, and the template or diagram itself are never sent. The question is the
   only user text included.
5. **Citations are an enum** of the audit's own pattern IDs.

**Post-validation** (`report/narrative.validate_points`) then drops any sentence
that:

- uses wording the product forbids ("novel", "proves", "impossible", "guarantee")
- names a pattern the audit does not cite
- quotes a pattern count that no connection or route hop in the audit has
- calls something "unsupported" when the audit has no `UNSUPPORTED` verdict

Each removal is reported in small print under the answer. A headline that fails
these checks falls back to the deterministic summary. A question that is only an
instruction to the model is refused without a model call. A question the audit
cannot answer, such as cost, is marked "Not covered by this audit" rather than
guessed.

## 7. The deterministic policy

These are the guarantees. Each one is enforced in code and covered by tests.

1. **No model in the core.** Parse, canonicalize, ground, rules, repair, similarity
   and report never call a model. The audit Lambda cannot reach Bedrock.
2. **Reproducible verdicts.** Every label is a pure function of the input graph,
   the pinned corpus commit and the rules table. The fingerprint proves it.
3. **Stable ordering.** Verdicts sort by service pair, closest patterns by score
   and then ID, retrieval by score, then pattern ID, then passage position, and
   the graph layout is deterministic too. Same input, same page.
4. **Model output is a draft.** Prose extraction returns a candidate graph that
   is repaired deterministically and confirmed by a person. Review and Q&A
   explain verdicts and cannot change them.
5. **Evidence before claims.** `UNSUPPORTED` needs a doc URL and a person's
   initials. "No precedent" is never presented as "impossible" or "novel". An
   unresolvable reference is a warning, not an edge. No evidenced route means no
   route is shown.
6. **Every rewrite is explained.** The post-processor records each change in
   `graph.warnings`, and the confirm screen shows them.
7. **Only prose goes to a model.** Templates and Mermaid never leave the audit
   Lambda. For review and Q&A, the model sees service-level facts, public README
   text and your question.
8. **Numbers come from the data.** Counts in the UI come from the API, and the
   corpus statistics come from `/corpus/stats`. Nothing is hardcoded.

## 8. Deployed architecture on AWS

One SAM template, `backend/infra/template.yaml`, stack `precedent` in `ap-south-1`:

| Service | Role |
|---|---|
| **Amplify Hosting** | the React build, HTTPS and the public URL |
| **API Gateway (HTTP API)** | one API in front of both functions, with CORS and throttling |
| **Lambda: audit** | parse, ground, repair, save; deterministic, no model access |
| **Lambda: extract** | prose drafting, review, Q&A; the only function that can reach Bedrock |
| **DynamoDB** | `Edges` (key `src#dst`: count and evidence), `Patterns`, `Audits` (30-day TTL) |
| **Secrets Manager** | the Bedrock API key, read by the extract function only |
| **Amazon Bedrock** | `zai.glm-4.7-flash` through the mantle endpoint |
| **S3** | an artifacts bucket, provisioned and private; not on the request path today |
| **CloudWatch and X-Ray** | structured logs and tracing on both functions |

Both functions are Python 3.12 on arm64. Grounding costs one `GetItem` per edge,
so audit latency does not grow with the corpus. The data files the core needs
are bundled into the package, so a cold start reads nothing from S3.

The split is deliberate: a Bedrock outage or a slow model degrades one tab and
the review, never an audit.

## 9. API reference

| Method | Path | Function | Purpose |
|---|---|---|---|
| GET | `/health` | audit | core status, corpus commit, vocabulary and rules counts |
| GET | `/corpus/stats` | audit | corpus and table statistics, limitations |
| GET | `/rules` | audit | the rules table with verification status |
| GET | `/patterns/{id}` | audit | one pattern and its edges |
| POST | `/audits` | audit | `{input_type, content}` or `{graph}` → `AuditReport` |
| GET | `/audits/{id}` | audit | a saved report |
| GET | `/extract/health` | extract | which model provider is configured |
| POST | `/extract` | extract | `{content}` prose → draft graph, `confirm_required: true` |
| POST | `/audits/{id}/review` | extract | review: headline, points with citations, sources |
| POST | `/audits/{id}/ask` | extract | `{question}` → answer points, `answerable`, sources |

Example:

```bash
curl -s -X POST https://ob72bcbovd.execute-api.ap-south-1.amazonaws.com/v1/audits \
  -H 'content-type: application/json' \
  -d '{"input_type":"mermaid","content":"flowchart LR\n A[S3 bucket] --> B[Step Functions]\n B --> C[Lambda]"}'
```

Errors come back as `{error, message}`. A template that fails to parse reports
the line and column.

`backend/api/app.py` is a FastAPI app that mirrors every route for local
development.

## 10. Frontend

`frontend/` is React 18 with Vite 5 and `@xyflow/react`, styled as a drafting
sheet: a paper grid, graphite ink, pastel washes that always carry meaning,
Space Grotesk and IBM Plex Mono.

| Piece | File |
|---|---|
| input tabs, samples, prose notice | `components/Workbench.jsx` |
| editable draft confirmation | `components/ConfirmPanel.jsx` |
| title block, verdict totals, results | `components/Results.jsx` |
| the drawing: graph with verdict edges | `components/ArchitectureGraph.jsx`, `lib/layout.js` |
| connection schedule with evidence and routes | `components/ConnectionSchedule.jsx` |
| review and Q&A | `components/Review.jsx` |
| API client and health merge | `api.js` |

The graph layout is a small deterministic layered layout (`lib/layout.js`):
break cycles, assign longest-path layers, pull each node right to sit just
before its nearest successor so no edge crosses an unrelated node, order nodes
by barycentre, then place. Review and Ask are hidden when the server reports no
model. The page works down to 390 px wide and honours reduced motion.

## 11. Repository layout

```
backend/
  core/            deterministic pipeline (no model imports)
    audit.py         the pipeline in one place
    template_parser.py, cfn_loader.py, mermaid_parser.py, canonicalize.py
    grounding.py     labels, node checks, summary
    rules.py         integration rules and the verification gate
    repair.py        weighted, relay-only routes
    similarity.py    IDF containment for closest patterns
    retrieve.py      BM25 over README passages
    extract_postprocess.py   rules A-D for model drafts
    store.py         SQLite locally, DynamoDB deployed, one interface
    models.py        pydantic data contracts and the fingerprint
  extract/llm/     model client, prompts, prose extraction
  report/          narrative.py: review and Q&A
  indexer/         census, build_index, readme_chunks, load_ddb
  lambda_src/      handler_audit.py, handler_extract.py
  api/             local FastAPI mirror
  infra/           template.yaml
  data/            vocabulary, aliases, rules, pattern index, README passages
  tests/           parser, core, report, api
  tasks.py         cross-platform task runner
frontend/          React app
docs/PLAN.md       design, stage checklists, rules appendix
```

## 12. Running, testing, deploying

Full setup, including the traps, is in [CONTRIBUTING.md](CONTRIBUTING.md). In short:

```bash
cd backend
python tasks.py corpus     # clone the pinned corpus (one time)
python tasks.py index      # build the local index
python tasks.py test       # pytest
python tasks.py api        # FastAPI on :8000
python tasks.py web        # frontend on :5173, proxies /api to :8000
```

Prose, review and Q&A need `LLM_PROVIDER=bedrock` and a key in `backend/.env`.
Without one, everything else works.

Deploy (project owner):

```bash
python tasks.py package    # Linux arm64 build, never `sam build`: it strips the compiled .so files
sam deploy --template infra/template.yaml --stack-name precedent --region ap-south-1 \
  --capabilities CAPABILITY_IAM --resolve-s3
python tasks.py load       # push the index to DynamoDB when it changes
python tasks.py web-deploy # build the frontend against the stack's API and publish to Amplify
```


