"""FastAPI application for local development and dev servers.

Mirrors the endpoints provided by the deployed Lambda handlers (handler_audit and handler_extract)
so frontend development and local API testing can run with `python tasks.py api` or `uvicorn api.app:app`.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Ensure .env is loaded if present
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_env_path = _BACKEND_ROOT / ".env"
if _env_path.exists():
    with open(_env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import boto3

from core.audit import InputError, build_graph, corpus_graph_from_pairs, run_audit
from core.cfn_loader import TemplateParseError
from core.extract_postprocess import postprocess
from core.grounding import LIMITATIONS, EdgeFacts
from core.models import ArchitectureGraph, Evidence
from core.rules import load as load_rules
from core.similarity import load_index
from core.store import DynamoStore, SqliteStore, open_store
from core.vocabulary import load as load_vocab
from extract.llm.client import (
    Client,
    DiskCache,
    LLMCacheMiss,
    LLMDisabled,
    LLMError,
    NoneProvider,
    from_env as llm_from_env,
)
from extract.llm.prose_extract import extract as prose_extract

app = FastAPI(
    title="Precedent API",
    description="Grounds AWS architecture connections in real deployable patterns.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global configuration & loaded datasets
_VOCAB = load_vocab()
_RULES = load_rules()
_PATTERN_INDEX = load_index()
_CORPUS_COMMIT = os.environ.get("CORPUS_COMMIT", "3d39819b0fdd2e38e42923db1e7c2fb8bcaa6dfe")
_CACHE = DiskCache(os.environ.get("LLM_CACHE_DIR", "/tmp/llm-cache"))
_MAX_INPUT_BYTES = 256 * 1024

EDGES_TABLE = os.environ.get("DDB_TABLE_EDGES", "")
PATTERNS_TABLE = os.environ.get("DDB_TABLE_PATTERNS", "")
AUDITS_TABLE = os.environ.get("DDB_TABLE_AUDITS", "")

# In-memory storage fallback for local audits
_LOCAL_AUDITS: dict[str, dict] = {}


def get_edge_store():
    """Returns DynamoStore if configured and reachable, else SqliteStore if exists."""
    if EDGES_TABLE:
        try:
            return DynamoStore(EDGES_TABLE)
        except Exception:
            pass
    # Resolve relative to backend/, not the current directory, so the API
    # finds its index however it is launched.
    db_path = Path(os.environ.get("DB_PATH", "data/precedent.sqlite"))
    if not db_path.is_absolute():
        db_path = _BACKEND_ROOT / db_path
    if db_path.exists():
        return open_store(db_path)
    # Return empty DynamoStore as fallback
    return DynamoStore("")


def get_corpus_graph():
    store = get_edge_store()
    try:
        pairs = store.all_pairs()
    except Exception:
        pairs = []
    return corpus_graph_from_pairs(pairs, _RULES)


class ExtractRequest(BaseModel):
    content: str
    input_type: Optional[str] = "prose"


class AuditRequest(BaseModel):
    input_type: Optional[str] = None
    content: Optional[str] = None
    graph: Optional[dict[str, Any]] = None


@app.get("/health")
def health():
    tables_ok = bool(EDGES_TABLE)
    stats = _RULES.stats()
    return {
        "core": "ok",
        "llm": os.environ.get("LLM_PROVIDER", "none"),
        "llm_ok": True,
        "corpus_commit": _CORPUS_COMMIT,
        "cold_start": False,
        "tables_ok": tables_ok,
        "vocabulary_services": len(_VOCAB.ids),
        "rules_total": stats["total"],
        "rules_verified": stats["verified"],
        "patterns_indexed": _PATTERN_INDEX.n if _PATTERN_INDEX else 0,
        "inputs": ["template", "mermaid", "graph"],
        "stage": "audit",
    }


@app.get("/corpus/stats")
def corpus_stats():
    edge_count = -1
    pattern_count = -1
    audit_count = -1

    if EDGES_TABLE:
        try:
            ddb = boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
            edge_count = ddb.describe_table(TableName=EDGES_TABLE)["Table"]["ItemCount"]
            if PATTERNS_TABLE:
                pattern_count = ddb.describe_table(TableName=PATTERNS_TABLE)["Table"]["ItemCount"]
            if AUDITS_TABLE:
                audit_count = ddb.describe_table(TableName=AUDITS_TABLE)["Table"]["ItemCount"]
        except Exception:
            pass

    return {
        "corpus_commit": _CORPUS_COMMIT,
        "corpus": "aws-samples/serverless-patterns",
        "edges_indexed": edge_count,
        "patterns_indexed": pattern_count,
        "audits_saved": audit_count,
        "vocabulary_services": len(_VOCAB.ids),
        "rules": _RULES.stats(),
        "limitations": LIMITATIONS,
        "note": "DynamoDB ItemCount refreshes roughly every six hours.",
    }


@app.get("/rules")
def rules_view():
    return {
        "stats": _RULES.stats(),
        "note": (
            "A rule only affects a verdict once a person has checked it against "
            "AWS documentation and added their initials. Unverified rules are "
            "listed here but inert."
        ),
        "rules": [
            {
                "rule_id": r.rule_id,
                "src": r.src_service,
                "dst": r.dst_service,
                "direct_supported": r.direct_supported,
                "verified": r.is_verified,
                "doc_url": r.doc_url,
                "note": r.note,
            }
            for r in _RULES.rules
        ],
    }


@app.get("/patterns/{pattern_id}")
def get_pattern(pattern_id: str):
    if not pattern_id:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    if PATTERNS_TABLE:
        try:
            ddb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
            item = ddb.Table(PATTERNS_TABLE).get_item(Key={"pattern_id": pattern_id}).get("Item")
            if item:
                out = dict(item)
                if _PATTERN_INDEX is not None:
                    edges = _PATTERN_INDEX.pattern_edges.get(pattern_id, set())
                    out["edges"] = [{"src": a, "dst": b} for a, b in sorted(edges)]
                return out
        except Exception as exc:
            print(f"pattern lookup error: {exc}")

    raise HTTPException(status_code=404, detail={"error": "not_found"})


@app.get("/extract/health")
def extract_health():
    return {
        "llm": os.environ.get("LLM_PROVIDER", "none"),
        "model": os.environ.get("BEDROCK_MODEL", "zai.glm-4.7-flash"),
        "cold_start": False,
    }


@app.post("/extract")
def extract_endpoint(req: ExtractRequest):
    started = time.time()
    if not req.content:
        raise HTTPException(status_code=400, detail={"error": "bad_request", "message": "content is required"})
    if req.input_type not in (None, "prose"):
        raise HTTPException(status_code=400, detail={"error": "bad_request", "message": "this endpoint extracts prose only"})

    try:
        client = llm_from_env()
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "llm_config_error", "message": str(exc)})

    try:
        graph = prose_extract(req.content, client, _VOCAB)
    except LLMDisabled as exc:
        raise HTTPException(status_code=503, detail={"error": "extraction_disabled", "message": str(exc)})
    except LLMCacheMiss as exc:
        raise HTTPException(status_code=503, detail={"error": "offline_cache_miss", "message": str(exc)})
    except LLMError as exc:
        raise HTTPException(status_code=503, detail={"error": "extraction_failed", "message": str(exc)})

    before = set(graph.service_edges())
    graph = postprocess(graph, _VOCAB)
    after = set(graph.service_edges())

    out = json.loads(graph.model_dump_json())
    out["confirm_required"] = True
    out["repairs_applied"] = {
        "removed": sorted(f"{a} -> {b}" for a, b in before - after),
        "added": sorted(f"{a} -> {b}" for a, b in after - before),
    }
    out["timings_ms"] = {"total": int((time.time() - started) * 1000)}
    out["cold_start"] = False
    return out


@app.post("/audits")
def create_audit(req: AuditRequest):
    started = time.time()
    t0 = time.time()
    try:
        if req.graph:
            graph = ArchitectureGraph.model_validate(req.graph)
        else:
            graph = build_graph(req.input_type, req.content, _VOCAB)
    except InputError as exc:
        raise HTTPException(status_code=400, detail={"error": "bad_request", "message": str(exc)})
    except TemplateParseError as exc:
        raise HTTPException(status_code=400, detail=exc.as_api_error())

    parse_ms = int((time.time() - t0) * 1000)

    store = get_edge_store()
    t1 = time.time()
    corpus_g = get_corpus_graph()
    report = run_audit(
        graph,
        store,
        corpus_g,
        corpus_commit=_CORPUS_COMMIT,
        vocab=_VOCAB,
        rules=_RULES,
        pattern_index=_PATTERN_INDEX,
    )
    ground_ms = int((time.time() - t1) * 1000)
    report.timings_ms = {
        "parse": parse_ms,
        "audit": ground_ms,
        "total": int((time.time() - started) * 1000),
    }

    out = json.loads(report.model_dump_json())
    _save_audit(report.audit_id, out)
    out["cold_start"] = False
    out["fingerprint"] = report.deterministic_fingerprint()
    return out


@app.get("/audits/{audit_id}")
def get_audit(audit_id: str):
    if not audit_id:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    if AUDITS_TABLE:
        try:
            ddb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
            item = ddb.Table(AUDITS_TABLE).get_item(Key={"audit_id": audit_id}).get("Item")
            if item:
                return json.loads(item["report"])
        except Exception:
            pass

    if audit_id in _LOCAL_AUDITS:
        return _LOCAL_AUDITS[audit_id]

    raise HTTPException(status_code=404, detail={"error": "not_found"})


def _save_audit(audit_id: str, payload: dict) -> None:
    _LOCAL_AUDITS[audit_id] = payload
    if AUDITS_TABLE:
        try:
            ddb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
            ddb.Table(AUDITS_TABLE).put_item(
                Item={
                    "audit_id": audit_id,
                    "report": json.dumps(payload),
                    "created_at": int(time.time()),
                    "ttl": int(time.time()) + 30 * 86400,
                }
            )
        except Exception as exc:
            print(f"failed to save audit to ddb: {exc}")
