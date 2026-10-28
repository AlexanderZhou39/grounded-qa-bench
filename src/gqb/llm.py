"""Provider-agnostic LLM client contract plus concrete adapters.

Every stage talks to models only through ``LLMClient`` so that (a) cost/latency is captured
per call and attributed to the calling stage (via ``StageMeter``), and (b) any stage can be
swapped to any model. ``CachedClient`` makes reruns free and deterministic.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, Field, ValidationError

from gqb.schemas import StageTrace

T = TypeVar("T", bound=BaseModel)


class Message(BaseModel):
    role: str  # user | assistant
    content: str
    images: list[bytes] = Field(default_factory=list, description="PNG bytes, sent before text")


class LLMResult(BaseModel):
    text: str
    parsed: Any | None = Field(None, description="Instance of response_model when requested")
    model: str
    in_tok: int = 0
    out_tok: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    cached: bool = False


class LLMClient(Protocol):
    def complete(
        self,
        messages: list[Message],
        model: str,
        *,
        system: str | None = None,
        response_model: type[T] | None = None,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> LLMResult: ...


# USD per 1M tokens: (input, output). Anthropic first-party list prices, cached 2026-10-06.
# Re-check against the provider pricing page before publishing cost numbers.
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}


def cost_usd(model: str, in_tok: int, out_tok: int) -> float:
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    return (in_tok * price_in + out_tok * price_out) / 1_000_000


# ---------------------------------------------------------------------------
# JSON helpers (used when a provider has no native structured output)
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in text (tolerates code fences and prose)."""
    m = _FENCE.search(text)
    if m:
        text = m.group(1)
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError("no JSON found")
    return json.JSONDecoder().raw_decode(text[start:])[0]


def schema_instruction(model: type[BaseModel]) -> str:
    return (
        "Respond with only a JSON object (no prose, no code fences) matching this JSON schema:\n"
        + json.dumps(model.model_json_schema())
    )


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


class AnthropicClient:
    """Claude via the official SDK. Structured output uses ``messages.parse``.

    API key: ``ANTHROPIC_API_KEY`` (or pass ``api_key``). Sampling params are not sent;
    current Claude models reject non-default temperature.
    """

    def __init__(self, api_key: str | None = None, max_retries: int = 4, timeout: float = 600):
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
            max_retries=max_retries,
            timeout=timeout,
        )

    @staticmethod
    def _to_api(messages: list[Message]) -> list[dict]:
        out = []
        for m in messages:
            if not m.images:
                out.append({"role": m.role, "content": m.content})
                continue
            blocks: list[dict] = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.standard_b64encode(img).decode(),
                    },
                }
                for img in m.images
            ]
            blocks.append({"type": "text", "text": m.content})
            out.append({"role": m.role, "content": blocks})
        return out

    def complete(
        self,
        messages: list[Message],
        model: str,
        *,
        system: str | None = None,
        response_model: type[T] | None = None,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> LLMResult:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": self._to_api(messages),
        }
        if system:
            kwargs["system"] = system
        if effort:
            kwargs["output_config"] = {"effort": effort}
        t0 = time.perf_counter()
        if response_model is not None:
            resp = self._client.messages.parse(output_format=response_model, **kwargs)
            parsed = resp.parsed_output
        else:
            resp = self._client.messages.create(**kwargs)
            parsed = None
        latency = (time.perf_counter() - t0) * 1000
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"model refused: {getattr(resp, 'stop_details', None)}")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        in_tok = resp.usage.input_tokens + (resp.usage.cache_read_input_tokens or 0)
        out_tok = resp.usage.output_tokens
        return LLMResult(
            text=text,
            parsed=parsed,
            model=model,
            in_tok=in_tok,
            out_tok=out_tok,
            cost_usd=cost_usd(model, in_tok, out_tok),
            latency_ms=latency,
        )


class OpenAICompatClient:
    """Any OpenAI-compatible chat endpoint (OpenRouter, vLLM, Gemini compat, ...).

    For non-Claude baselines. Structured output via JSON instruction + pydantic validation.
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key)

    def complete(
        self,
        messages: list[Message],
        model: str,
        *,
        system: str | None = None,
        response_model: type[T] | None = None,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> LLMResult:
        api_msgs: list[dict] = []
        sys_text = system or ""
        if response_model is not None:
            sys_text = (sys_text + "\n\n" + schema_instruction(response_model)).strip()
        if sys_text:
            api_msgs.append({"role": "system", "content": sys_text})
        for m in messages:
            if m.images:
                parts: list[dict] = [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64," + base64.b64encode(img).decode()
                        },
                    }
                    for img in m.images
                ]
                parts.append({"type": "text", "text": m.content})
                api_msgs.append({"role": m.role, "content": parts})
            else:
                api_msgs.append({"role": m.role, "content": m.content})
        t0 = time.perf_counter()
        resp = self._client.chat.completions.create(
            model=model, messages=api_msgs, max_tokens=max_tokens
        )
        latency = (time.perf_counter() - t0) * 1000
        text = resp.choices[0].message.content or ""
        parsed = None
        if response_model is not None:
            parsed = response_model.model_validate(extract_json(text))
        usage = resp.usage
        in_tok = usage.prompt_tokens if usage else 0
        out_tok = usage.completion_tokens if usage else 0
        return LLMResult(
            text=text,
            parsed=parsed,
            model=model,
            in_tok=in_tok,
            out_tok=out_tok,
            cost_usd=cost_usd(model, in_tok, out_tok),
            latency_ms=latency,
        )


class CachedClient:
    """Disk cache keyed on the full request. Cache hits report zero cost but keep the
    original token counts and latency in ``meta`` so reports can show *uncached* cost."""

    def __init__(self, inner: LLMClient, cache_dir: str | Path = ".cache/llm", salt: str = ""):
        self.inner = inner
        self.salt = salt  # e.g. repeat index, so consistency repeats are real samples
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _key(self, messages, model, system, response_model, max_tokens, effort) -> str:
        h = hashlib.sha256()
        payload = {
            "salt": self.salt,
            "model": model,
            "system": system,
            "schema": response_model.model_json_schema() if response_model else None,
            "max_tokens": max_tokens,
            "effort": effort,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "images": [hashlib.sha256(i).hexdigest() for i in m.images],
                }
                for m in messages
            ],
        }
        h.update(json.dumps(payload, sort_keys=True).encode())
        return h.hexdigest()

    def complete(
        self,
        messages: list[Message],
        model: str,
        *,
        system: str | None = None,
        response_model: type[T] | None = None,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> LLMResult:
        key = self._key(messages, model, system, response_model, max_tokens, effort)
        path = self.dir / key[:2] / f"{key}.json"
        if path.exists():
            res = LLMResult.model_validate_json(path.read_text())
            if response_model is not None and res.parsed is not None:
                res.parsed = response_model.model_validate(res.parsed)
            return res.model_copy(update={"cached": True})
        res = self.inner.complete(
            messages,
            model,
            system=system,
            response_model=response_model,
            max_tokens=max_tokens,
            effort=effort,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        dumped = res.model_dump(mode="json")
        if isinstance(res.parsed, BaseModel):
            dumped["parsed"] = res.parsed.model_dump(mode="json")
        path.write_text(json.dumps(dumped))
        return res


class FakeClient:
    """Deterministic client for tests: ``responder(messages, system, model)`` returns text or
    a dict (dumped to JSON). Token counts are approximated as chars/4."""

    def __init__(self, responder: Callable[[list[Message], str | None, str], Any]):
        self.responder = responder
        self.calls: list[tuple[str, list[Message]]] = []

    def complete(
        self,
        messages: list[Message],
        model: str,
        *,
        system: str | None = None,
        response_model: type[T] | None = None,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> LLMResult:
        self.calls.append((model, messages))
        out = self.responder(messages, system, model)
        text = out if isinstance(out, str) else json.dumps(out)
        parsed = None
        if response_model is not None:
            try:
                parsed = response_model.model_validate(extract_json(text))
            except (ValueError, ValidationError) as exc:
                raise RuntimeError(f"fake response does not match schema: {exc}") from exc
        in_tok = sum(len(m.content) for m in messages) // 4 + len(system or "") // 4
        out_tok = len(text) // 4
        return LLMResult(
            text=text,
            parsed=parsed,
            model=model,
            in_tok=in_tok,
            out_tok=out_tok,
            cost_usd=cost_usd(model, in_tok, out_tok),
            latency_ms=1.0,
        )


# ---------------------------------------------------------------------------
# Per-stage accounting
# ---------------------------------------------------------------------------


class StageMeter:
    """Wraps an LLMClient for one stage invocation and accumulates a StageTrace.

    Usage::

        with StageMeter(llm, slot="verify", impl="llm_judge", model=m) as meter:
            res = meter.complete(...)
        state.trace.append(meter.trace)
    """

    def __init__(self, llm: LLMClient, slot: str, impl: str, model: str | None = None):
        self.llm = llm
        self.trace = StageTrace(stage=slot, impl=impl, model=model)
        self._lock = threading.Lock()
        self._t0 = 0.0
        self.cached_calls = 0

    def __enter__(self) -> StageMeter:
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.trace.latency_ms = (time.perf_counter() - self._t0) * 1000
        if exc is not None:
            self.trace.error = repr(exc)

    def complete(self, messages: list[Message], model: str, **kw: Any) -> LLMResult:
        res = self.llm.complete(messages, model, **kw)
        with self._lock:
            self.trace.calls += 1
            self.trace.in_tok += res.in_tok
            self.trace.out_tok += res.out_tok
            # cost is attributed even on cache hits: we report the cost of the method,
            # not of this particular rerun
            self.trace.cost_usd += res.cost_usd
            if res.cached:
                self.cached_calls += 1
                # replace wall time of cached calls with the recorded original latency
                self._t0 -= res.latency_ms / 1000
        return res


class RoutingClient:
    """Routes by model name: ``claude-*`` -> Anthropic, anything else -> OpenAI-compatible
    endpoint (``OPENAI_BASE_URL`` / ``OPENAI_API_KEY``). Clients are created lazily."""

    def __init__(self) -> None:
        self._anthropic: AnthropicClient | None = None
        self._openai: OpenAICompatClient | None = None

    def complete(self, messages: list[Message], model: str, **kw: Any) -> LLMResult:
        if model.startswith("claude-"):
            self._anthropic = self._anthropic or AnthropicClient()
            return self._anthropic.complete(messages, model, **kw)
        self._openai = self._openai or OpenAICompatClient()
        return self._openai.complete(messages, model, **kw)


# ---------------------------------------------------------------------------
# Pluggable backends
# ---------------------------------------------------------------------------


class ModelMapClient:
    """Wraps any backend: maps the benchmark's logical model ids (``claude-haiku-5-5``, ...)
    to backend ids via ``model_map``, and fills ``cost_usd`` at public list prices (``PRICES``,
    keyed by the logical id) when the backend reports none. Results keep the logical id, so
    cache keys, configs and reports do not change when the backend does."""

    def __init__(self, inner: LLMClient, model_map: dict[str, str] | None = None):
        self.inner = inner
        self.model_map = model_map or {}

    def complete(
        self,
        messages: list[Message],
        model: str,
        *,
        system: str | None = None,
        response_model: type[T] | None = None,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> LLMResult:
        res = self.inner.complete(
            messages,
            self.model_map.get(model, model),
            system=system,
            response_model=response_model,
            max_tokens=max_tokens,
            effort=effort,
        )
        cost = res.cost_usd or cost_usd(model, res.in_tok, res.out_tok)
        return res.model_copy(update={"model": model, "cost_usd": cost})


class PendingAnswer(RuntimeError):
    """Raised by FileQueueClient for a request that has no answer file yet."""


class FileQueueClient:
    """Backend for answering requests out of band, e.g. by a coding agent or a human.

    Each unanswered request is written to ``{root}/requests/{key}.json`` (system, messages,
    model, and the JSON schema when structured output is required) and ``PendingAnswer`` is
    raised. Whoever answers writes ``{root}/answers/{key}.json`` as ``{"text": "..."}``. For
    structured requests the text must be a JSON object matching the schema. Re-running the
    same command then picks the answers up. Token counts are estimated as chars/4 so that
    cost columns still show public-rate equivalents."""

    def __init__(self, root: str | Path = ".queue"):
        self.root = Path(root)

    def complete(
        self,
        messages: list[Message],
        model: str,
        *,
        system: str | None = None,
        response_model: type[T] | None = None,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> LLMResult:
        req = {
            "model": model,
            "system": system,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "schema": response_model.model_json_schema() if response_model else None,
            "max_tokens": max_tokens,
        }
        key = hashlib.sha256(json.dumps(req, sort_keys=True).encode()).hexdigest()[:24]
        ans = self.root / "answers" / f"{key}.json"
        if not ans.exists():
            path = self.root / "requests" / f"{key}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(req, indent=1))
            raise PendingAnswer(f"PendingAnswer {key}")
        text = json.loads(ans.read_text())["text"]
        parsed = response_model.model_validate(extract_json(text)) if response_model else None
        in_tok = (sum(len(m.content) for m in messages) + len(system or "")) // 4
        out_tok = len(text) // 4
        return LLMResult(
            text=text,
            parsed=parsed,
            model=model,
            in_tok=in_tok,
            out_tok=out_tok,
            cost_usd=cost_usd(model, in_tok, out_tok),
        )


def load_client() -> LLMClient:
    """Backend selection, by environment:

    - ``GQB_LLM_CLIENT=package.module:factory``: any zero-arg callable returning an object with
      the ``LLMClient.complete`` signature (your own gateway, a proxy, ...)
    - ``GQB_LLM_CLIENT=queue`` (optionally ``GQB_QUEUE_DIR``): ``FileQueueClient``
    - unset: ``RoutingClient`` (Anthropic SDK for ``claude-*``, OpenAI-compatible otherwise)

    ``GQB_MODEL_MAP`` (JSON object) maps logical model ids to backend ids."""
    import importlib

    spec = os.environ.get("GQB_LLM_CLIENT", "").strip()
    if spec == "queue":
        inner: LLMClient = FileQueueClient(os.environ.get("GQB_QUEUE_DIR", ".queue"))
    elif spec:
        mod, _, attr = spec.partition(":")
        inner = getattr(importlib.import_module(mod), attr or "make_client")()
    else:
        inner = RoutingClient()
    return ModelMapClient(inner, json.loads(os.environ.get("GQB_MODEL_MAP", "{}") or "{}"))
