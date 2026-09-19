"""Audit Lambda. Deterministic only, never calls a model.

Thin by design: this module does HTTP and DynamoDB, and calls core.audit for
everything else. The pipeline is tested locally without deploying, and nothing
reachable from here can call an LLM.

Prose extraction lives in a separate function precisely so a Bedrock outage or a
slow model can never make an audit fail. See section 4 of docs/PLAN.md.
"""

from __future__ import annotations

import base64
import json
import os
import time

import boto3

from core.audit import InputError, build_graph, corpus_graph_from_pairs, run_audit
from core.grounding import LIMITATIONS, EdgeFacts
from core.models import ArchitectureGraph, Evidence
from core.rules import load as load_rules
from core.similarity import load_index
from core.vocabulary import load as load_vocab
from core.cfn_loader import TemplateParseError

_ddb = boto3.resource("dynamodb")
_COLD_START = True

# Loaded once per container, reused across invocations.
_VOCAB = load_vocab()
_RULES = load_rules()
_PATTERN_INDEX = load_index()   # None if the file is absent; degrades to []
_CORPUS_GRAPH = None

EDGES_TABLE = os.environ.get("DDB_TABLE_EDGES", "")
PATTERNS_TABLE = os.environ.get("DDB_TABLE_PATTERNS", "")
AUDITS_TABLE = os.environ.get("DDB_TABLE_AUDITS", "")
CORPUS_COMMIT = os.environ.get("CORPUS_COMMIT", "unknown")
MAX_INPUT_BYTES = 256 * 1024
AUDIT_TTL_DAYS = 30


class DynamoEdgeStore:
    """One GetItem per edge, memoised per request."""

    def __init__(self, table_name: str):
        self._table = _ddb.Table(table_name) if table_name else None
        self._cache: dict[tuple[str, str], EdgeFacts] = {}

    def facts(self, src_service: str, dst_service: str) -> EdgeFacts:
        key = (src_service, dst_service)
        if key in self._cache:
            return self._cache[key]
        facts = EdgeFacts()
        if self._table is not None:
            try:
                item = self._table.get_item(
                    Key={"pair": f"{src_service}#{dst_service}"}
                ).get("Item")
                if item:
                    facts = EdgeFacts(
                        count=int(item.get("count", 0)),
                        evidence=tuple(
                            Evidence(pattern_id=e.get("pattern_id", ""),
                                     title=e.get("title", ""),
                                     url=e.get("url", ""))
                            for e in item.get("evidence", [])
                        ),
                    )
            except Exception as exc:  # a lookup failure must not fail the audit
                print(json.dumps({"level": "warn", "lookup": f"{src_service}#{dst_service}",
                                  "error": repr(exc)}))
        self._cache[key] = facts
        return facts


def _corpus_graph():
    """Scan the edge table once per container to build the repair graph.

    58 items today. If this table ever grows past a few thousand, move the
    scan into the indexer and store the graph as an S3 artifact instead.
    """
    global _CORPUS_GRAPH
    if _CORPUS_GRAPH is not None:
        return _CORPUS_GRAPH
    pairs: list[tuple[str, str, int]] = []
    if EDGES_TABLE:
        try:
            table = _ddb.Table(EDGES_TABLE)
            kwargs = {"ProjectionExpression": "src_service, dst_service, #c",
                      "ExpressionAttributeNames": {"#c": "count"}}
            while True:
                page = table.scan(**kwargs)
                for item in page.get("Items", []):
                    pairs.append((item["src_service"], item["dst_service"],
                                  int(item.get("count", 0))))
                if "LastEvaluatedKey" not in page:
                    break
                kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        except Exception as exc:
            print(json.dumps({"level": "warn", "corpus_graph": repr(exc)}))
    _CORPUS_GRAPH = corpus_graph_from_pairs(pairs, _RULES)
    return _CORPUS_GRAPH


def _response(status: int, body) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(body, default=str),
    }


def _route(event: dict) -> tuple[str, str]:
    ctx = event.get("requestContext", {}).get("http", {})
    return ctx.get("method", "GET"), ctx.get("path", "/")


def _payload(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", errors="replace")
    if len(raw.encode("utf-8")) > MAX_INPUT_BYTES * 2:
        raise InputError("too_large")
    return json.loads(raw)


def handler(event, context):  # noqa: ANN001 - Lambda signature
    global _COLD_START
    cold = _COLD_START
    _COLD_START = False
    started = time.time()
    method, path = _route(event)

    try:
        if path.endswith("/health"):
            body = _health(cold)
        elif path.endswith("/corpus/stats"):
            body = _corpus_stats()
        elif path.endswith("/rules"):
            body = _rules_view()
        elif "/patterns/" in path:
            found = _get_pattern(path.rsplit("/", 1)[-1])
            if found is None:
                return _response(404, {"error": "not_found"})
            return _response(200, found)
        elif path.endswith("/audits") and method == "POST":
            return _response(200, _create_audit(_payload(event), started, cold))
        elif "/audits/" in path:
            found = _get_audit(path.rsplit("/", 1)[-1])
            if found is None:
                return _response(404, {"error": "not_found"})
            return _response(200, found)
        else:
            return _response(404, {"error": "not_found", "path": path})
    except InputError as exc:
        if str(exc) == "too_large":
            return _response(413, {"error": "too_large",
                                   "message": f"Input exceeds {MAX_INPUT_BYTES} bytes."})
        return _response(400, {"error": "bad_request", "message": str(exc)})
    except TemplateParseError as exc:
        return _response(400, exc.as_api_error())
    except json.JSONDecodeError as exc:
        return _response(400, {"error": "bad_json", "message": str(exc)})
    except Exception as exc:  # never leak a stack trace to a public URL
        print(json.dumps({"level": "error", "path": path, "error": repr(exc)}))
        return _response(500, {"error": "internal_error"})

    body["timings_ms"] = {"total": int((time.time() - started) * 1000)}
    return _response(200, body)


def _health(cold: bool) -> dict:
    tables_ok = True
    try:
        _ddb.meta.client.describe_table(TableName=EDGES_TABLE)
    except Exception:
        tables_ok = False
    stats = _RULES.stats()
    return {
        "core": "ok",
        "llm": "none",
        "llm_ok": False,
        "corpus_commit": CORPUS_COMMIT,
        "cold_start": cold,
        "tables_ok": tables_ok,
        "vocabulary_services": len(_VOCAB.ids),
        "rules_total": stats["total"],
        "rules_verified": stats["verified"],
        "patterns_indexed": _PATTERN_INDEX.n if _PATTERN_INDEX else 0,
        "inputs": ["template", "mermaid"],
        "stage": "audit",
    }


def _corpus_stats() -> dict:
    client = _ddb.meta.client

    def count(name: str) -> int:
        if not name:
            return 0
        try:
            return client.describe_table(TableName=name)["Table"]["ItemCount"]
        except Exception:
            return -1

    return {
        "corpus_commit": CORPUS_COMMIT,
        "corpus": "aws-samples/serverless-patterns",
        "edges_indexed": count(EDGES_TABLE),
        "patterns_indexed": count(PATTERNS_TABLE),
        "audits_saved": count(AUDITS_TABLE),
        "vocabulary_services": len(_VOCAB.ids),
        "rules": _RULES.stats(),
        "limitations": LIMITATIONS,
        "note": "DynamoDB ItemCount refreshes roughly every six hours.",
    }


def _rules_view() -> dict:
    """Every rule, with its verification state. Nothing hidden."""
    return {
        "stats": _RULES.stats(),
        "note": ("A rule only affects a verdict once a person has checked it against "
                 "AWS documentation and added their initials. Unverified rules are "
                 "listed here but inert."),
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


def _create_audit(payload: dict, started: float, cold: bool) -> dict:
    t0 = time.time()
    if payload.get("graph"):
        graph = ArchitectureGraph.model_validate(payload["graph"])
    else:
        graph = build_graph(payload.get("input_type"), payload.get("content"), _VOCAB)
    parse_ms = int((time.time() - t0) * 1000)

    store = DynamoEdgeStore(EDGES_TABLE)
    t1 = time.time()
    report = run_audit(
        graph, store, _corpus_graph(),
        corpus_commit=CORPUS_COMMIT, vocab=_VOCAB, rules=_RULES,
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
    out["cold_start"] = cold
    out["fingerprint"] = report.deterministic_fingerprint()
    return out


def _save_audit(audit_id: str, payload: dict) -> None:
    if not AUDITS_TABLE:
        return
    try:
        _ddb.Table(AUDITS_TABLE).put_item(Item={
            "audit_id": audit_id,
            "report": json.dumps(payload),
            "created_at": int(time.time()),
            "ttl": int(time.time()) + AUDIT_TTL_DAYS * 86400,
        })
    except Exception as exc:  # a shareable link is nice, not essential
        print(json.dumps({"level": "warn", "save": audit_id, "error": repr(exc)}))


def _get_pattern(pattern_id: str):
    """Pattern detail, so the UI can render evidence without leaving the app."""
    if not pattern_id:
        return None
    if PATTERNS_TABLE:
        try:
            item = _ddb.Table(PATTERNS_TABLE).get_item(
                Key={"pattern_id": pattern_id}
            ).get("Item")
            if item:
                out = dict(item)
                if _PATTERN_INDEX is not None:
                    edges = _PATTERN_INDEX.pattern_edges.get(pattern_id, set())
                    out["edges"] = [{"src": a, "dst": b} for a, b in sorted(edges)]
                return out
        except Exception as exc:
            print(json.dumps({"level": "warn", "pattern": pattern_id, "error": repr(exc)}))
    return None


def _get_audit(audit_id: str):
    if not AUDITS_TABLE or not audit_id:
        return None
    item = _ddb.Table(AUDITS_TABLE).get_item(Key={"audit_id": audit_id}).get("Item")
    if not item:
        return None
    return json.loads(item["report"])
