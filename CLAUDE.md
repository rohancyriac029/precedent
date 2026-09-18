# Project rules for coding agents

Read docs/PLAN.md before any change. Work only on the current stage.

Hard rules:
1. The core pipeline (parse, canonicalize, ground, check, repair, report) must NEVER call an LLM.
   LLM code lives only in extract/llm/ and report/narrative.py, and is always optional.
2. Every verdict label must be computed deterministically and be reproducible from the same input
   and the same corpus commit.
3. The only model provider is AWS Bedrock, via the mantle OpenAI-compatible endpoint,
   with "ollama" as an offline development fallback and "none" as a valid mode.
   No other hosted provider, and no vendor SDK: the call is a plain httpx POST.
4. The Bedrock key comes from Secrets Manager in the deployed app and from .env locally.
   Never an environment variable in the template, never in the repo, never in the plan.
   Only prose input is ever sent to a model. Templates and Mermaid never leave the audit Lambda.
5. No embeddings, no torch, no sentence-transformers in the Lambda runtime.
   Canonicalization falls back to difflib, and evidence retrieval uses BM25.
6. Every LLM call goes through extract/llm/client.py (cache + retry + schema validation),
   and every call passes a JSON schema with strict: true. Never call a model without one.
7. Do not add dependencies outside the approved list in docs/PLAN.md section 5 without asking.
8. Write tests before or with each parser rule. Use fixtures in tests/fixtures/.
9. Never hardcode metrics or counts in the UI or slides. All numbers come from eval/ outputs.
10. When a stage is done, tick the boxes in docs/PLAN.md and summarize what is NOT done.
11. LLM output is a draft, never a verdict. extract/llm/ returns a candidate graph;
    core/extract_postprocess.py repairs it deterministically; the user confirms it in the
    UI before any audit runs.

## Track

SHIP IT. The deliverable is a deployed app with a public URL, not a laptop tool.
Deployment is on the critical path, not a stretch goal. See section 1 of the plan for
the table of which named AWS services we use and which we deliberately do not.

## Running things

```
make test          # pytest
make census        # corpus stats (needs make corpus first)
make api           # local FastAPI on :8000
```

## Conventions

- `core/` is pure and deterministic. It may import `data/`, never `extract/`.
- Service-level edges `(src_service, dst_service)` are the unit everything agrees on:
  parsers emit them, labels are computed on them, evals score them.
- Anything the post-processor rewrites must be recorded in `graph.warnings` so the
  confirmation UI can explain itself.
