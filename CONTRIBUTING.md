# Contributing to Precedent

Everything you need to go from a fresh `git clone` to a running app on your
machine. Every step here was run on a clean clone before it was written down.

You do **not** need an AWS account to develop locally. You only need one to deploy.

---

## Repository layout

```
precedent/
├── backend/     Python: the audit pipeline, the API, the indexer, the AWS stack, tests
├── frontend/    the web app (Vite)
└── docs/        PLAN.md, the full plan
```

Python commands run from `backend/`. Node commands run from `frontend/`.

---

## 1. Install these first

| Tool | Version | Needed for | Check |
|---|---|---|---|
| Git | any recent | cloning, and the corpus | `git --version` |
| Python | **3.12 or newer** | backend, tests, indexer | `python --version` |
| Node.js | **20 LTS** (18 minimum) | frontend | `node --version` |

Optional, only if you need them:

| Tool | Needed for |
|---|---|
| AWS CLI v2 + SAM CLI | deploying. Local development never needs them. |
| Ollama with `qwen2.5:7b` | offline prose extraction without a Bedrock key |

`make` is not required. It is usually missing on Windows, so every command below
uses `python tasks.py <task>`, which works the same on any OS.

---

## 2. Backend setup

### Clone, then create a virtual environment inside `backend/`

```bash
git clone https://github.com/rohancyriac029/precedent.git
cd precedent/backend
python -m venv .venv
```

Activate it:

```bash
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Windows (Git Bash)
source .venv/Scripts/activate
# macOS / Linux
source .venv/bin/activate
```

### Install Python dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

`requirements-dev.txt` pulls in `requirements.txt`, so this one command installs
everything: pydantic, pyyaml, cfn-flip, networkx, rank-bm25, httpx, mangum,
fastapi, uvicorn, pytest and boto3.

### Check it works

```bash
python tasks.py test
```

Every test should pass. The tests need no network, no AWS and no corpus.

---

## 3. Build the local corpus index — do not skip this

The app counts how often each connection appears in real AWS patterns. Those
counts come from a local database, `backend/data/precedent.sqlite`, which is
**not in the repository** because it is generated. Build it once, from `backend/`:

```bash
python tasks.py corpus    # clones aws-samples/serverless-patterns, ~200 MB, a few minutes
python tasks.py index     # parses it into data/precedent.sqlite, under a minute
```

**Why this matters.** Without the index, `POST /audits` returns a
`500 Internal Server Error` on a machine with no AWS setup. It is the first thing
you will hit if you skip this step.

Re-run `python tasks.py index` whenever you change the parser or
`data/vocabulary.yaml`, otherwise your counts are stale.

---

## 4. Environment file

The API reads `backend/.env`. Create it from the example, inside `backend/`:

```bash
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
```

Then edit `backend/.env` and change these three lines:

```dotenv
LLM_PROVIDER=none
DDB_TABLE_EDGES=
DDB_TABLE_AUDITS=
```

**Blank the two `DDB_TABLE_*` lines. This one is easy to miss and it fails silently.**
The example file names tables that do not exist on your machine. If you have ever
run `aws configure`, the app will try those tables, every lookup will fail
quietly, and every connection will come back as `UNPRECEDENTED_IN_CORPUS` with a
count of 0 and a `200 OK`. It looks like a working app giving wrong answers.
With the lines blank, it uses your local index instead.

`LLM_PROVIDER=none` is correct unless you have a Bedrock key. Template and
Mermaid input work fully without one. Only the prose tab needs a model; with
`none` it returns `503 extraction_disabled`, which is expected. To enable it, set
`LLM_PROVIDER=bedrock` and `BEDROCK_API_KEY=...`.

**Never commit `.env`.** It is in `.gitignore`. A Bedrock key goes in
`backend/.env` and nowhere else.

---

## 5. Frontend setup

```bash
cd ../frontend       # from backend/
npm ci
```

Use `npm ci` rather than `npm install`, so you get exactly the versions in
`package-lock.json`.

---

## 6. Run it

Two terminals.

**Terminal 1, the API**, from `backend/` with the virtual environment active:

```bash
python tasks.py api
```

Serves `http://127.0.0.1:8000` with auto-reload. Check `http://127.0.0.1:8000/health`.

**Terminal 2, the frontend**, from `frontend/`:

```bash
npm run dev
```

Or, from `backend/`, `python tasks.py web` does the same thing.

Open `http://localhost:5173`. The dev server forwards every `/api/...` request to
the API on port 8000, so start the API first.

To check a production build, from `frontend/`: `npm run build`.

---

## 7. Where things live

| Path | What it is |
|---|---|
| `backend/core/` | the deterministic pipeline: parsing, grounding, rules, repair, similarity |
| `backend/extract/llm/` | prose extraction. The only code that calls a model. |
| `backend/indexer/` | corpus census, the SQLite index, the DynamoDB loader |
| `backend/api/app.py` | local FastAPI server for development |
| `backend/lambda_src/` | the deployed Lambda handlers |
| `backend/infra/template.yaml` | the AWS stack |
| `backend/data/` | vocabulary, aliases, integration rules, pattern index |
| `backend/tests/` | pytest suite |
| `frontend/src/` | the web app |
| `docs/PLAN.md` | the full plan. Read it before a large change. |

---

## 8. Rules that keep the product honest

These are enforced by review and by tests. The full list is in `CLAUDE.md`.

1. **`backend/core/` never calls a model.** Every verdict must be reproducible from
   the same input and the same corpus commit. Model code lives only in
   `backend/extract/llm/`.
2. **Every model call passes a JSON schema.** No schema, no call.
3. **Model output is a draft.** Prose is extracted, repaired deterministically,
   and confirmed by the user before any audit runs. Do not audit raw prose.
4. **Never guess an edge.** A reference the parser cannot resolve becomes a
   warning, not an invented connection. A wrong edge inflates a precedent count.
5. **No torch, sentence-transformers or vendor model SDKs** in the Lambda
   runtime. Ask before adding any dependency.
6. **Add a test with every parser rule.** `backend/tests/parser/` has one per rule.
7. **Integration rules need a doc URL and your initials** in `verified_by`
   before they affect a verdict. Only add initials after checking the doc.

---

## 9. Git workflow

* Branch from `dev`, not `main`: `git checkout dev && git checkout -b feat/your-thing`
* Open your pull request into **`dev`**.
* `main` is what is deployed. Changes reach it from `dev` once reviewed.
* Run `python tasks.py test` in `backend/` before pushing.

---

## 10. Deploying (owner only)

You do not need this to contribute. Deployment uses the project's AWS account.
From `backend/`:

```bash
python tasks.py package
sam deploy --template infra/template.yaml --stack-name precedent \
  --region ap-south-1 --capabilities CAPABILITY_IAM --resolve-s3
```

**Do not run `sam build`.** It silently strips the compiled `.so` files from the
package, and the Lambda then fails at cold start with
`No module named pydantic_core._pydantic_core`. `tasks.py package` builds the
correct Linux arm64 package itself, which is why it works from Windows without
Docker.

The frontend is a separate step, also from `backend/`:

```bash
python tasks.py web-deploy
```

It reads `ApiUrl` from the `precedent` stack, builds `frontend/` with
`VITE_API_BASE` set to it, and publishes the build to the Amplify app
`precedent` (branch `main`) as a manual deployment. It needs `boto3` and
the same AWS credentials. Deploy the backend first if the API changed.

---

## 11. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `POST /audits` returns 500 | no local index | in `backend/`: `python tasks.py corpus` then `python tasks.py index` |
| every edge is `UNPRECEDENTED_IN_CORPUS`, count 0 | `DDB_TABLE_*` set in `backend/.env` | blank both lines, restart the API |
| prose tab returns 503 | `LLM_PROVIDER=none` | expected; set a Bedrock key or run Ollama |
| frontend shows network errors | API not running | start `python tasks.py api` first |
| counts look out of date | stale index | `python tasks.py index` |
| `make: command not found` | normal on Windows | use `python tasks.py <task>` |
| `ModuleNotFoundError` | wrong folder, or venv not active | run from `backend/` with `.venv` active |
| `python tasks.py web` says file not found | old checkout | pull; the task now resolves `npm.cmd` on Windows |
