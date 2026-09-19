"""Extract Lambda. The only function that talks to a model.

Deliberately separate from the audit function. Extraction can be slow and can
fail; audits must be fast and must never fail because a model is unavailable.
Keeping them apart means a Bedrock outage degrades one tab, not the product.

The response is always a DRAFT graph plus warnings. It is never audited here.
The user confirms it first, which is the control that makes the prose path safe:
the schema enum prevents hallucinated services, but no model tested preserved
the correct edge set under prompt injection.
"""

from __future__ import annotations

import base64
import json
import os
import time

import boto3

from core.extract_postprocess import postprocess
from core.vocabulary import load as load_vocab
from extract.llm.client import (
    BedrockProvider,
    Client,
    DiskCache,
    LLMCacheMiss,
    LLMDisabled,
    LLMError,
    NoneProvider,
)
from extract.llm.prose_extract import extract

_VOCAB = load_vocab()
_COLD_START = True
_SECRET_CACHE: dict[str, str] = {}

SECRET_ID = os.environ.get("BEDROCK_API_KEY_SECRET", "")
BASE_URL = os.environ.get("BEDROCK_BASE_URL", "")
MODEL = os.environ.get("BEDROCK_MODEL", "zai.glm-4.7-flash")
TIMEOUT = float(os.environ.get("LLM_TIMEOUT_S", "25"))
MAX_INPUT_BYTES = 64 * 1024   # prose, not templates: a design brief is small

# Lambda's filesystem is read-only apart from /tmp, and /tmp survives across
# warm invocations, so the cache is worth having even here.
CACHE = DiskCache(os.environ.get("LLM_CACHE_DIR", "/tmp/llm-cache"))


def _api_key() -> str:
    """Fetch and memoise the key. Never an environment variable."""
    if not SECRET_ID:
        return ""
    if SECRET_ID in _SECRET_CACHE:
        return _SECRET_CACHE[SECRET_ID]
    sm = boto3.client("secretsmanager")
    value = sm.get_secret_value(SecretId=SECRET_ID)["SecretString"]
    try:
        parsed = json.loads(value)
        key = parsed.get("api_key") or parsed.get("BEDROCK_API_KEY") or value
    except json.JSONDecodeError:
        key = value
    _SECRET_CACHE[SECRET_ID] = key
    return key


def _client() -> Client:
    try:
        key = _api_key()
    except Exception as exc:
        print(json.dumps({"level": "error", "secret": repr(exc)}))
        key = ""
    if not key or not BASE_URL:
        return Client(NoneProvider(), cache=CACHE)
    return Client(
        BedrockProvider(base_url=BASE_URL, api_key=key, model=MODEL),
        cache=CACHE,
        timeout=TIMEOUT,
    )


def _response(status: int, body) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(body, default=str),
    }


def _payload(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", errors="replace")
    if len(raw.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("too_large")
    return json.loads(raw)


def handler(event, context):  # noqa: ANN001 - Lambda signature
    global _COLD_START
    cold = _COLD_START
    _COLD_START = False
    started = time.time()

    ctx = event.get("requestContext", {}).get("http", {})
    path = ctx.get("path", "/")

    if path.endswith("/extract/health"):
        return _response(200, {
            "llm": "bedrock" if (BASE_URL and SECRET_ID) else "none",
            "model": MODEL,
            "cold_start": cold,
        })

    try:
        payload = _payload(event)
    except ValueError:
        return _response(413, {"error": "too_large",
                               "message": f"Input exceeds {MAX_INPUT_BYTES} bytes."})
    except json.JSONDecodeError as exc:
        return _response(400, {"error": "bad_json", "message": str(exc)})

    content = payload.get("content")
    if not content or not isinstance(content, str):
        return _response(400, {"error": "bad_request",
                               "message": "content is required"})
    if payload.get("input_type") not in (None, "prose"):
        return _response(400, {"error": "bad_request",
                               "message": "this endpoint extracts prose only"})

    try:
        graph = extract(content, _client(), _VOCAB)
    except LLMDisabled as exc:
        return _response(503, {"error": "extraction_disabled", "message": str(exc)})
    except LLMCacheMiss as exc:
        return _response(503, {"error": "offline_cache_miss", "message": str(exc)})
    except LLMError as exc:
        print(json.dumps({"level": "error", "extract": repr(exc)}))
        return _response(503, {"error": "extraction_failed",
                               "message": "The model was unreachable. "
                                          "Template and Mermaid input still work."})
    except Exception as exc:
        print(json.dumps({"level": "error", "extract": repr(exc)}))
        return _response(500, {"error": "internal_error"})

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
    out["cold_start"] = cold
    return _response(200, out)
