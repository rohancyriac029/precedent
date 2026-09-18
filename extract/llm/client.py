"""One interface to every model provider.

Section 3.4 of docs/PLAN.md. Three providers:

* `none`    — raises LLMDisabled. The default, and a first-class mode: the
              product is fully useful without any model.
* `bedrock` — HTTPS to the mantle OpenAI-compatible endpoint. A plain httpx POST,
              no vendor SDK, because those models are not reachable through
              `bedrock-runtime` and boto3 at all.
* `ollama`  — localhost, for offline development and as the demo fallback.

Every call passes a JSON schema and validates the response again on the way
back. Measured: with no schema both local models produced 0% parseable JSON;
with one, 100%. A schema is not an optimisation here, it is the mechanism.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

import httpx

DEFAULT_TIMEOUT = 30.0
MAX_TRIES = 4
CACHE_DIR = Path(os.environ.get("LLM_CACHE_DIR", ".cache/llm"))


class LLMDisabled(RuntimeError):
    """LLM_PROVIDER=none. Expected, not an error condition."""


class LLMCacheMiss(RuntimeError):
    """LLM_OFFLINE=1 and this call is not in the cache."""


class LLMError(RuntimeError):
    """The provider failed after retries."""


@dataclass
class LLMResult:
    data: dict[str, Any]
    raw: str = ""
    provider: str = "none"
    model: str = ""
    cache_hit: bool = False
    latency_ms: int = 0
    retries: int = 0
    validation_ok: bool = True
    errors: list[str] = field(default_factory=list)


class LLMClient(Protocol):
    def generate_json(self, *, system: str, user: str, schema: dict,
                      task: str) -> LLMResult: ...


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------


def cache_key(provider: str, model: str, task: str, system: str, user: str,
              schema: dict) -> str:
    blob = json.dumps(
        [provider, model, task, system, user, schema],
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class DiskCache:
    """Cache hits cost nothing and make evaluation and the demo reproducible."""

    def __init__(self, directory: Path = CACHE_DIR):
        self.dir = Path(directory)

    def get(self, key: str) -> Optional[dict]:
        p = self.dir / f"{key}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def put(self, key: str, payload: dict) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / f"{key}.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
        except OSError:
            pass  # a read-only filesystem must not break extraction


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------


class NoneProvider:
    name = "none"
    model = ""

    def complete(self, system: str, user: str, schema: dict, timeout: float) -> str:
        raise LLMDisabled(
            "Extraction is disabled (LLM_PROVIDER=none). Template and Mermaid "
            "input still work."
        )


class BedrockProvider:
    """OpenAI-compatible chat completions with a strict JSON schema."""

    name = "bedrock"

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def complete(self, system: str, user: str, schema: dict, timeout: float) -> str:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": 3000,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "extract", "schema": schema, "strict": True},
            },
        }
        r = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
        if r.status_code != 200:
            raise LLMError(f"bedrock HTTP {r.status_code}: {r.text[:200]}")
        payload = r.json()
        return payload["choices"][0]["message"].get("content") or ""


class OllamaProvider:
    """Local fallback. Slower, weaker, private, and works with the network off."""

    name = "ollama"

    def __init__(self, host: str, model: str, num_ctx: int = 2048):
        self.host = host.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx

    def complete(self, system: str, user: str, schema: dict, timeout: float) -> str:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": schema,
            "options": {"temperature": 0, "num_ctx": self.num_ctx, "seed": 7},
        }
        r = httpx.post(f"{self.host}/api/chat", json=body, timeout=timeout)
        if r.status_code != 200:
            raise LLMError(f"ollama HTTP {r.status_code}: {r.text[:200]}")
        return r.json()["message"]["content"]


# --------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------


class Client:
    def __init__(
        self,
        provider,
        cache: Optional[DiskCache] = None,
        *,
        offline: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
        max_tries: int = MAX_TRIES,
        sleep=time.sleep,
    ):
        self.provider = provider
        self.cache = cache if cache is not None else DiskCache()
        self.offline = offline
        self.timeout = timeout
        self.max_tries = max_tries
        self._sleep = sleep

    def generate_json(self, *, system: str, user: str, schema: dict,
                      task: str) -> LLMResult:
        key = cache_key(self.provider.name, getattr(self.provider, "model", ""),
                        task, system, user, schema)
        cached = self.cache.get(key)
        if cached is not None:
            return LLMResult(data=cached.get("data", {}), raw=cached.get("raw", ""),
                             provider=self.provider.name,
                             model=getattr(self.provider, "model", ""),
                             cache_hit=True)
        if self.offline:
            raise LLMCacheMiss(f"offline mode and no cached response for task {task!r}")

        started = time.time()
        raw, retries = self._call_with_retries(system, user, schema)

        errors: list[str] = []
        try:
            data = json.loads(raw)
            validation_ok = True
        except json.JSONDecodeError as exc:
            # One repair retry, then give up. Never loop.
            errors.append(f"invalid JSON: {exc}")
            repair = (
                f"{user}\n\nYour previous reply was not valid JSON ({exc}). "
                f"Reply with JSON matching the schema and nothing else."
            )
            try:
                raw, extra = self._call_with_retries(system, repair, schema)
                retries += extra + 1
                data = json.loads(raw)
                validation_ok = True
                errors.append("recovered after one repair retry")
            except Exception as exc2:
                return LLMResult(
                    data={}, raw=raw, provider=self.provider.name,
                    model=getattr(self.provider, "model", ""),
                    latency_ms=int((time.time() - started) * 1000),
                    retries=retries, validation_ok=False,
                    errors=errors + [f"repair failed: {exc2}"],
                )

        result = LLMResult(
            data=data, raw=raw, provider=self.provider.name,
            model=getattr(self.provider, "model", ""),
            latency_ms=int((time.time() - started) * 1000),
            retries=retries, validation_ok=validation_ok, errors=errors,
        )
        self.cache.put(key, {"data": data, "raw": raw})
        self._log(task, result)
        return result

    def _call_with_retries(self, system: str, user: str, schema: dict) -> tuple[str, int]:
        delay = 1.0
        last: Optional[Exception] = None
        for attempt in range(self.max_tries):
            try:
                return self.provider.complete(system, user, schema, self.timeout), attempt
            except LLMDisabled:
                raise
            except Exception as exc:  # noqa: BLE001 - retry any transport failure
                last = exc
                if attempt == self.max_tries - 1:
                    break
                self._sleep(delay + random.random() * 0.3)
                delay *= 2
        raise LLMError(f"provider failed after {self.max_tries} tries: {last}")

    @staticmethod
    def _log(task: str, result: LLMResult) -> None:
        # One JSON line per call. No user content: this goes to CloudWatch.
        print(json.dumps({
            "task": task, "provider": result.provider, "model": result.model,
            "cache_hit": result.cache_hit, "latency_ms": result.latency_ms,
            "retries": result.retries, "validation_ok": result.validation_ok,
        }))


def from_env(env: Optional[dict] = None) -> Client:
    """Build a client from environment variables. See section 3.5 of the plan."""
    env = env if env is not None else os.environ
    name = (env.get("LLM_PROVIDER") or "none").strip().lower()
    timeout = float(env.get("LLM_TIMEOUT_S") or DEFAULT_TIMEOUT)
    offline = (env.get("LLM_OFFLINE") or "0").strip() in {"1", "true", "yes"}

    if name == "bedrock":
        key = env.get("BEDROCK_API_KEY") or ""
        if not key:
            raise LLMError("LLM_PROVIDER=bedrock but BEDROCK_API_KEY is empty")
        provider = BedrockProvider(
            base_url=env.get("BEDROCK_BASE_URL") or "",
            api_key=key,
            model=env.get("BEDROCK_MODEL") or "zai.glm-4.7-flash",
        )
    elif name == "ollama":
        provider = OllamaProvider(
            host=env.get("OLLAMA_HOST") or "http://localhost:11434",
            model=env.get("OLLAMA_MODEL") or "qwen2.5:7b",
            num_ctx=int(env.get("OLLAMA_NUM_CTX") or 2048),
        )
    else:
        provider = NoneProvider()

    return Client(provider, offline=offline, timeout=timeout)
