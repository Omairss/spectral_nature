"""Provider execution accounting and account-wide terminal-failure recovery.

Research semantics stay in AQL/Zopedia. This boundary records actual attempts,
preserves causal ownership, and stops rejected requests against an empty account.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
from functools import lru_cache, partial, wraps
import json
import logging
import os
import re
import threading
import time
from typing import Any
import uuid
from pathlib import Path
from urllib.parse import urlparse

import anyio
from anyio.from_thread import start_blocking_portal
import httpx
import requests

log = logging.getLogger(__name__)
_CALL: ContextVar[dict[str, Any]] = ContextVar("llm_execution_call", default={})
_ACCOUNTS: dict[str, tuple[float, bool]] = {}
_ACCOUNT_LOCK = threading.RLock()
_LOCAL_EXECUTION: ContextVar["LocalResearchExecution | None"] = ContextVar("local_research_execution", default=None)
ProviderResponse = requests.Response | httpx.Response


class ProviderHTTPDeadlineExceeded(TimeoutError):
    """The entire transport operation exceeded its caller's remaining time."""


class ProviderHTTPSession:
    """Synchronous provider interface backed by cancellable asynchronous I/O.

    Requests' read timeout measures inactivity, so a response that keeps sending
    bytes can exceed the caller's deadline. The cancel scope below covers the
    complete HTTP exchange. Each call owns and closes its client and portal;
    timing out never abandons a synchronous network thread in the background.
    """

    async def _request(self, method: str, deadline: float, kwargs: dict[str, Any]) -> httpx.Response:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderHTTPDeadlineExceeded('Provider HTTP deadline expired before sending')
        # Preserve Requests' environment CA override as well as HTTPX's own
        # SSL_CERT_FILE / proxy handling. No auth or cookies persist across calls.
        ca_bundle = os.getenv('REQUESTS_CA_BUNDLE') or os.getenv('CURL_CA_BUNDLE')
        verify: Any = True
        if ca_bundle:
            import ssl
            verify = ssl.create_default_context(**{
                'capath' if Path(ca_bundle).is_dir() else 'cafile': ca_bundle})
        try:
            with anyio.fail_after(max(0, deadline - time.monotonic())):
                async with httpx.AsyncClient(follow_redirects=True, verify=verify) as client:
                    return await client.request(method, **kwargs, timeout=httpx.Timeout(remaining))
        except TimeoutError as exc:
            raise ProviderHTTPDeadlineExceeded('Provider HTTP wall-clock deadline expired') from exc
        except httpx.ReadTimeout as exc:
            raise requests.exceptions.ReadTimeout(str(exc)) from exc
        except httpx.TransportError as exc:
            raise requests.exceptions.ConnectionError(str(exc)) from exc

    def request(self, method: str, *, timeout: float, **kwargs: Any) -> httpx.Response:
        deadline = time.monotonic() + float(timeout)
        # A portal works from synchronous callers, agent worker threads and
        # existing application event loops without nesting asyncio.run().
        with start_blocking_portal() as portal:
            return portal.call(partial(self._request, method, deadline, kwargs))

    def post(self, *, timeout: float, **kwargs: Any) -> httpx.Response:
        return self.request('POST', timeout=timeout, **kwargs)

    def get(self, url: str, *, timeout: float, **kwargs: Any) -> httpx.Response:
        return self.request('GET', url=url, timeout=timeout, **kwargs)


class ResearchSpendExceeded(RuntimeError):
    """The next request cannot be funded within the declared experiment budget."""


class LocalResearchExecution:
    """Explicit local storage and frozen settings for shared-engine experiments.

    Application execution keeps its normal stores. An activated experiment owns
    its receipts, provider state and analytical results under one local root.
    Threads inherit this context through the engine's existing copy_context calls.
    """

    def __init__(self, root: Path, *, overrides: dict[str, str], budget_usd: float,
                 input_usd_per_million: float, output_usd_per_million: float,
                 max_input_tokens: int = 1024 * 1024, max_output_tokens: int = 384 * 1024,
                 cost_basis: str = "provider_token_rates",
                 unknown_charge_policy: str = "halt",
                 request_max_output_tokens: int | None = None,
                 max_provider_attempts: int | None = None,
                 retain_training_io: bool = False):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if not isinstance(retain_training_io, bool):
            raise ValueError("Training transcript retention must be explicitly enabled or disabled")
        self.retain_training_io = retain_training_io
        if retain_training_io:
            self.root.chmod(0o700)
        self.overrides = dict(overrides)
        self.budget_usd = float(budget_usd)
        self.input_rate = float(input_usd_per_million)
        self.output_rate = float(output_usd_per_million)
        self.max_input_tokens = int(max_input_tokens)
        self.max_output_tokens = int(max_output_tokens)
        self.request_max_output_tokens = (int(request_max_output_tokens)
                                          if request_max_output_tokens is not None else None)
        self.max_provider_attempts = (int(max_provider_attempts)
                                      if max_provider_attempts is not None else None)
        self.cost_basis = cost_basis
        if unknown_charge_policy not in {"halt", "reserve_maximum"}:
            raise ValueError("Unknown charge policy must halt or retain the declared maximum exposure")
        self.unknown_charge_policy = unknown_charge_policy
        if min(self.max_input_tokens, self.max_output_tokens) <= 0:
            raise ValueError("Positive token limits are required")
        if self.request_max_output_tokens is not None and not (
            0 < self.request_max_output_tokens <= self.max_output_tokens
        ):
            raise ValueError("The request output limit must fit the declared model output capacity")
        if self.max_provider_attempts is not None and self.max_provider_attempts <= 0:
            raise ValueError("A positive provider attempt limit is required")
        if cost_basis == "provider_token_rates":
            if min(self.budget_usd, self.input_rate, self.output_rate) <= 0:
                raise ValueError("Positive budget and conservative token rates are required")
        elif cost_basis == "local_resources_unpriced":
            if (self.budget_usd, self.input_rate, self.output_rate) != (0, 0, 0):
                raise ValueError("Local unpriced execution declares zero external provider billing")
        else:
            raise ValueError("Unknown experiment cost basis")
        self._lock = threading.RLock()
        self._reservations: dict[str, float] = {}
        self._charges: dict[str, float] = {}
        self._charge_bounds: dict[str, float] = {}
        self._usage: dict[str, dict[str, Any]] = {}
        self._unknown: set[str] = set()
        self._pre_execution_rejections: dict[str, dict[str, Any]] = {}
        receipt_path = self.root / "provider_attempts.jsonl"
        policy_path = self.root / "accounting-policy.json"
        events = [json.loads(line) for line in receipt_path.read_text().splitlines()] if receipt_path.exists() else []
        if cost_basis == "local_resources_unpriced" and any(event.get("provider") != "local" for event in events):
            raise ValueError("A local ledger cannot reinterpret hosted or unidentified provider attempts")
        policy = {"version": 1, "cost_basis": cost_basis}
        if policy_path.exists():
            if json.loads(policy_path.read_text()) != policy:
                raise ValueError("The retained ledger accounting basis cannot change")
        else:
            # Exclusive creation fails closed if another owner races setup.
            with policy_path.open("x", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(json.dumps(policy) + "\n")
        if receipt_path.exists():
            for event in events:
                self._account(event)
            # An interrupted start has no trustworthy zero-cost interpretation.
            self._unknown.update(self._reservations)
            self._reservations.clear()
        self.record("accounting_activations", {
            "at_utc": datetime.now(timezone.utc).isoformat(), "budget_usd": self.budget_usd,
            "unknown_charge_policy": self.unknown_charge_policy,
            "input_token_limit": self.max_input_tokens, "output_token_limit": self.max_output_tokens,
            "input_rate": self.input_rate, "output_rate": self.output_rate,
        })

    @contextmanager
    def activate(self):
        token = _LOCAL_EXECUTION.set(self)
        try:
            yield self
        finally:
            _LOCAL_EXECUTION.reset(token)

    def record(self, kind: str, value: dict[str, Any]) -> None:
        with self._lock:
            with (self.root / (kind + ".jsonl")).open("a", encoding="utf-8") as stream:
                if self.retain_training_io:
                    os.fchmod(stream.fileno(), 0o600)
                stream.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            if kind == "provider_attempts":
                self._account(value)

    def _account(self, event: dict[str, Any]) -> None:
        if self.cost_basis == "local_resources_unpriced" and event.get("provider") != "local":
            raise ValueError("Unpriced local accounting requires local provider receipts")
        key = event["attempt_id"]
        if event["status"] == "started":
            maximum = (event.get("metadata") or {}).get("max_output_tokens") or self.max_output_tokens
            self._charge_bounds.setdefault(key, self._maximum_charge(maximum))
            self._reservations.setdefault(key, self._charge_bounds[key])
            return
        self._reservations.pop(key, None)
        usage = event.get("usage") or {}
        self._usage[key] = {"prompt_tokens": usage.get("prompt_tokens"),
                            "completion_tokens": usage.get("completion_tokens"),
                            "duration_ms": event.get("duration_ms", 0)}
        if event["status"] == "not_sent":
            self._charges[key] = 0.0
            self._unknown.discard(key)
        elif (event["status"] == "failure" and event.get("provider") == "deepseek"
              and event.get("http_status") == 402):
            # The same explicit balance rejection that blocks this provider
            # occurs before model generation. Keep missing usage visible, but
            # do not confuse it with an accepted request of unknown cost.
            self._charges[key] = 0.0
            self._unknown.discard(key)
            self._pre_execution_rejections[key] = {
                "provider": "deepseek", "http_status": 402, "reason": "insufficient_balance",
            }
        elif usage.get("prompt_tokens") is None or usage.get("completion_tokens") is None:
            self._unknown.add(key)
            # Older ledgers may contain a final receipt without its start.
            # Reserve the declared model capacity when no narrower request cap
            # was retained. The missing usage never becomes a zero charge.
            self._charge_bounds.setdefault(key, self._maximum_charge(
                (event.get("metadata") or {}).get("max_output_tokens") or self.max_output_tokens))
        else:
            self._charges[key] = (usage["prompt_tokens"] * self.input_rate
                                  + usage["completion_tokens"] * self.output_rate) / 1e6
            self._unknown.discard(key)

    def _maximum_charge(self, output_tokens: int) -> float:
        maximum = int(output_tokens)
        if not 0 < maximum <= self.max_output_tokens:
            raise ResearchSpendExceeded("Request output capacity exceeds its declared accounting bound")
        return (self.max_input_tokens * self.input_rate + maximum * self.output_rate) / 1e6

    def reserve(self, attempt_id: str, payload: dict[str, Any]) -> None:
        # Reserve the declared model limits, including any server-side message
        # framing, rather than inferring a token upper bound from JSON bytes.
        maximum = int(payload.get("max_tokens") or self.max_output_tokens)
        bound = self._maximum_charge(maximum)
        with self._lock:
            if self._unknown and self.cost_basis == "provider_token_rates" and self.unknown_charge_policy == "halt":
                raise ResearchSpendExceeded("Earlier provider charge is unknown; further inference is stopped")
            if self.max_provider_attempts is not None and (
                len(set(self._usage) | set(self._reservations) | self._unknown) >= self.max_provider_attempts
            ):
                raise ResearchSpendExceeded("The declared provider attempt limit is exhausted")
            unknown_bound = sum(self._charge_bounds[key] for key in self._unknown)
            if sum(self._charges.values()) + unknown_bound + sum(self._reservations.values()) + bound > self.budget_usd:
                raise ResearchSpendExceeded("The next request cannot fit within the declared experiment budget")
            self._reservations[attempt_id] = bound
            self._charge_bounds[attempt_id] = bound

    def validate_transport(self, config: Any) -> None:
        if self.cost_basis == "local_resources_unpriced":
            from urllib.parse import urlparse
            target = urlparse(config.base_url)
            if config.provider != "local" or target.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("Unpriced local accounting requires a loopback local provider")

    def summary(self) -> dict[str, Any]:
        with self._lock:
            result = {"budget_usd": self.budget_usd, "conservative_usage_estimate_usd": sum(self._charges.values()),
                    "priced_attempts": len(self._charges), "unknown_attempts": sorted(self._unknown),
                    "pending_attempts": sorted(self._reservations), "rates_per_million": {
                        "input": self.input_rate, "output": self.output_rate},
                    "reservation_limits": {"input_tokens": self.max_input_tokens, "output_tokens": self.max_output_tokens},
                    "basis": "all input at cache-miss peak rate; output includes reasoning; estimate, not invoice"}
            unknown_bound = sum(self._charge_bounds[key] for key in self._unknown)
            result.update(unknown_charge_policy=self.unknown_charge_policy,
                unknown_charge_upper_bound_usd=unknown_bound,
                pending_reservation_usd=sum(self._reservations.values()),
                accounted_spend_upper_bound_usd=sum(self._charges.values()) + unknown_bound,
                inference_blocked_by_unknown_charge=bool(self._unknown and
                    self.cost_basis == "provider_token_rates" and self.unknown_charge_policy == "halt"))
            result["unknown_usage_attempts"] = sorted(set(self._unknown) | {
                key for key, usage in self._usage.items()
                if usage.get("prompt_tokens") is None or usage.get("completion_tokens") is None
            })
            result["pre_execution_rejections"] = dict(self._pre_execution_rejections)
            if self.request_max_output_tokens is not None:
                result["request_max_output_tokens"] = self.request_max_output_tokens
            if self.max_provider_attempts is not None:
                result["max_provider_attempts"] = self.max_provider_attempts
            if self.cost_basis == "local_resources_unpriced":
                result.update({"conservative_usage_estimate_usd": None,
                    "external_provider_charge_usd": 0, "local_resource_cost_usd": None,
                    "cost_basis": self.cost_basis, "priced_attempts": 0,
                    "metered_attempts": len(self._usage),
                    "unknown_usage_attempts": sorted(self._unknown), "unknown_charge_attempts": [],
                    "tokens": {field: sum(u[field] or 0 for u in self._usage.values())
                               for field in ("prompt_tokens", "completion_tokens")},
                    "provider_wall_seconds": sum(u["duration_ms"] for u in self._usage.values()) / 1000,
                    "basis": "Loopback inference; external API billing is zero. Local hardware, energy and opportunity cost are unpriced. Missing usage remains unknown."})
            return result

    def read(self, namespace: str, key: str) -> dict[str, Any] | None:
        path = self._state_path(namespace, key)
        return json.loads(path.read_text()) if path.exists() else None

    def write(self, namespace: str, key: str, value: dict[str, Any]) -> None:
        path = self._state_path(namespace, key)
        with self._lock:
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, default=str))
            temporary.replace(path)

    def _state_path(self, namespace: str, key: str) -> Path:
        identity = hashlib.sha256(json.dumps([namespace, key]).encode()).hexdigest()
        return self.root / ("state_" + identity + ".json")


def current_local_execution() -> LocalResearchExecution | None:
    return _LOCAL_EXECUTION.get()



class LLMContextWindowExceeded(RuntimeError):
    """Capacity feedback for the agent; retrying an unchanged request cannot help."""


def request_context_budget(config: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Preflight the complete request, including schemas and output headroom.

    cl100k is an estimate for other tokenizers, not provider-reported usage.
    Leave headroom for that difference and handle exact provider feedback too.
    Unknown deployments must declare their window through configuration.
    """
    window = int(os.getenv("LLM_CONTEXT_WINDOW_TOKENS") or (
        1048576 if config.provider == "deepseek" and
        urlparse(config.base_url).hostname == "api.deepseek.com" else 0
    ))
    if not window or not payload.get("messages"):
        return {}
    import tiktoken

    encoding = tiktoken.get_encoding("cl100k_base")
    text = json.dumps(payload, ensure_ascii=False, default=str)
    tokens = len(encoding.encode_ordinary(text))
    reserve = int(payload.get("max_tokens") or os.getenv("LLM_CONTEXT_OUTPUT_RESERVE_TOKENS") or 65536)
    estimate = (tokens * 5 + 3) // 4
    return {"window_tokens": window, "estimated_input_tokens": estimate,
            "reserved_output_tokens": reserve, "fits": estimate + reserve <= window,
            "measurement": "cl100k estimate with 25% tokenizer headroom; provider usage is authoritative"}


@contextmanager
def model_call_context(**metadata: Any):
    token = _CALL.set({**_CALL.get(), **metadata})
    try:
        yield
    finally:
        _CALL.reset(token)


def execution_effort(default: str = "") -> str:
    return str(_CALL.get().get("inference_effort") or default)


def preserves_literal_output() -> bool:
    return bool(_CALL.get().get("literal_output"))


def current_research_access_scope() -> str:
    """A producer declares ownership; private API/research workers default off."""
    return os.getenv("LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE", "").strip()


@contextmanager
def research_run_context(run_id: str):
    parent = _CALL.get()
    with model_call_context(root_run_id=parent.get("root_run_id") or os.getenv("PIPELINE_RUN_ID") or run_id,
                            parent_run_id=parent.get("run_id"), run_id=run_id):
        yield


@lru_cache(maxsize=1)
def _price_snapshot() -> dict[str, Any]:
    path = Path(os.getenv("LLM_PRICE_SNAPSHOT_PATH") or Path(__file__).with_name("llm_prices.json"))
    return json.loads(path.read_text())


def estimated_charge(*, provider: str, model: str, usage: dict[str, Any] | None,
                     started_at: datetime) -> dict[str, Any]:
    """Versioned estimate, not an invoice; unknown usage/model is never zero."""
    if not usage:
        return {"status": "usage_unavailable"}
    try:
        snapshot = _price_snapshot()
        price = snapshot["models"].get(provider + ":" + model)
        if price is None:
            return {"status": "price_unavailable"}
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        if input_tokens is None:
            return {"status": "usage_unavailable"}
        cached = usage.get("prompt_cache_hit_tokens", (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
        output = usage.get("completion_tokens", usage.get("output_tokens", 0))
        rates = price["offpeak"]
        base = ((input_tokens-cached)*rates["input"] + cached*rates["cached_input"] + output*rates["output"]) / 1e6
        utc = started_at.astimezone(timezone.utc)
        peak = utc.weekday() < 5 and any(start <= utc.hour < end for start, end in price.get("peak_utc_hours", []))
        return {"status": "estimated", "price_version": snapshot["version"], "source": price["source"],
                "usd_min": base, "usd_max": base * price.get("peak_multiplier", 1) if peak else base,
                "rate_band": "peak_or_holiday" if peak else "offpeak"}
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "price_unavailable"}


def _account_key(config: Any) -> str:
    # Only the digest is retained. It identifies the billing account across
    # development/production workers without persisting credentials.
    return hashlib.sha256(f"{config.provider}:{config.base_url}:{config.api_key}".encode()).hexdigest()


def _read_state(namespace: str, key: str) -> dict[str, Any] | None:
    if local := current_local_execution():
        return local.read(namespace, key)
    from .pipeline_store import read_execution_state
    try:
        return read_execution_state(namespace, key)
    except Exception:
        log.warning("LLM execution checkpoint read unavailable", exc_info=False)
        return None


def _write_state(namespace: str, key: str, value: dict[str, Any]) -> None:
    if local := current_local_execution():
        local.write(namespace, key, value)
        return
    from .pipeline_store import write_execution_state
    try:
        if not write_execution_state(namespace, key, value):
            log.warning("LLM execution checkpoint was not persisted")
    except Exception:
        log.warning("LLM execution checkpoint write unavailable", exc_info=False)


def ensure_provider_available(config: Any, *, timeout: float) -> None:
    if config.provider != "deepseek":
        return
    from .llm import LLMProviderUnavailable
    key = _account_key(config)
    with _ACCOUNT_LOCK:
        now = time.monotonic()
        local = _ACCOUNTS.get(key)
        if local and now - local[0] < 30:
            blocked = local[1]
        else:
            state = _read_state("provider_account", key)
            blocked = bool((state or {}).get("blocked")) or bool(local and local[1])
            # An absent/stale local flag is not proof that a prepaid account
            # has credit. Probe once per cache interval, and recover a blocked
            # account only from an actual positive provider response.
            try:
                response = ProviderHTTPSession().get(
                    config.base_url.rstrip("/") + "/user/balance",
                    headers={"Authorization": "Bearer " + config.api_key},
                    timeout=min(timeout, 15),
                )
                if 200 <= response.status_code < 300:
                    available = response.json().get("is_available")
                    if isinstance(available, bool):
                        blocked = not available
                        _write_state("provider_account", key, {"blocked": blocked})
            except (requests.RequestException, ProviderHTTPDeadlineExceeded, ValueError):
                pass
            _ACCOUNTS[key] = (time.monotonic(), blocked)
    if blocked:
        raise LLMProviderUnavailable("DeepSeek account has insufficient funds; provider execution is paused until credit is restored.")


def request_composition(payload: dict[str, Any]) -> dict[str, Any]:
    """Measure the final transport payload without retaining any prompt text."""
    encoder = json.JSONEncoder(ensure_ascii=True, default=str)
    def size(value: Any) -> int:
        return sum(len(part) for part in encoder.iterencode(value))
    roles: dict[str, int] = {}
    messages = payload.get("messages") or []
    for message in messages:
        role = str(message.get("role") or "unknown")
        roles[role] = roles.get(role, 0) + size(message)
    return {"serialized_characters": size(payload), "message_count": len(messages),
            "message_characters_by_role": roles, "tool_count": len(payload.get("tools") or []),
            "tool_schema_characters": size(payload.get("tools") or []),
            "response_schema_characters": size(payload.get("response_format") or {}),
            "measurement": "serialized JSON characters; provider usage supplies actual tokens"}


def provider_post(client: Any = None, *, config: Any = None, session: Any = None,
                  operation: str = "chat", **kwargs: Any) -> ProviderResponse:
    """Meter each transport attempt, including failures and nested JSON repairs."""
    from .llm import LLMDeadlineExceeded, llm_settings_scope, _remaining_request_timeout
    from .pipeline_store import record_provider_attempt
    config = config or client.config
    session = session or client.session
    local = current_local_execution()
    if local:
        local.validate_transport(config)
    timeout = float(kwargs.get("timeout") or config.timeout_seconds)
    deadline = time.monotonic() + timeout
    ensure_provider_available(config, timeout=timeout)
    kwargs["timeout"] = _remaining_request_timeout(deadline)
    context = dict(_CALL.get())
    payload = dict(kwargs.get("json") or {})
    if local and local.request_max_output_tokens is not None and operation in {"chat", "json_repair"}:
        requested = payload.get("max_tokens")
        if requested is not None and (
            not isinstance(requested, int) or isinstance(requested, bool)
            or requested <= 0 or requested > local.request_max_output_tokens
        ):
            raise ValueError("Request max_tokens violates the frozen research output limit")
        if requested is None:
            payload["max_tokens"] = local.request_max_output_tokens
        kwargs["json"] = payload
    if config.provider == "deepseek" and (operation == "json_repair" or context.get("literal_output")):
        # Non-thinking defaults to 8K while thinking defaults to 64K. A
        # mechanical conversion must not silently shorten an existing artifact.
        payload.setdefault("max_tokens", int(os.getenv("DEEPSEEK_LITERAL_MAX_OUTPUT_TOKENS") or "65536"))
        kwargs["json"] = payload
    composition = request_composition(payload)
    context_budget = request_context_budget(config, payload)
    if context_budget:
        composition["context_budget"] = context_budget
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    attempt_id = "llmhttp_" + uuid.uuid4().hex
    if local:
        local.reserve(attempt_id, payload)
        record_provider_attempt = lambda event: local.record("provider_attempts", event) or True
    request_metadata = {
        "request_composition": composition, "job_name": os.getenv("PIPELINE_JOB_NAME"),
        "input_hash": hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest(),
        "inference_effort": payload.get("reasoning_effort"), "thinking": payload.get("thinking"),
        "max_output_tokens": payload.get("max_tokens"),
        "sampling_parameters": {name: payload[name] for name in (
            "temperature", "top_p", "top_k", "min_p", "seed", "n",
            "presence_penalty", "frequency_penalty", "max_tokens",
            "max_completion_tokens", "thinking_token_budget",
        ) if name in payload},
    }
    initial_event = {
        "attempt_id": attempt_id, "model_call_id": context.get("model_call_id"),
        "root_run_id": context.get("root_run_id") or os.getenv("PIPELINE_RUN_ID") or context.get("run_id"),
        "parent_run_id": context.get("run_id") or context.get("parent_run_id"),
        "settings_scope": llm_settings_scope(),
        "surface": context.get("surface") or getattr(client, "_zopedia_surface", "") or "provider.direct",
        "purpose": context.get("purpose") or "direct." + operation,
        "provider": config.provider, "requested_model": payload.get("model") or config.model,
        "operation": operation, "status": "started", "started_at_utc": started, "duration_ms": 0,
        "metadata": request_metadata,
    }
    try:
        if not record_provider_attempt(initial_event):
            log.warning("Provider attempt start could not be persisted: %s", attempt_id)
    except Exception:
        log.warning("Provider attempt start accounting unavailable: %s", attempt_id)
    response = None
    error_type = ""
    transport_attempted = False
    try:
        if context_budget and not context_budget["fits"]:
            raise LLMContextWindowExceeded(json.dumps(context_budget))
        if local and local.retain_training_io and operation in {"chat", "json_repair"}:
            # Deliberate private research material, separate from operational
            # telemetry. Never retain transport headers or private reasoning.
            request = {name: payload[name] for name in (
                "model", "response_format", "tools", "tool_choice", "max_tokens",
                "max_completion_tokens", "temperature", "top_p", "top_k", "min_p",
                "seed", "n", "stop", "presence_penalty", "frequency_penalty",
                "thinking", "reasoning_effort",
            ) if name in payload}
            request["messages"] = [
                {name: message[name] for name in ("role", "content", "name", "tool_call_id", "tool_calls")
                 if name in message}
                for message in (payload.get("messages") or []) if isinstance(message, dict)
            ]
            local.record("provider_inputs", {
                "attempt_id": attempt_id, "model_call_id": context.get("model_call_id"),
                "run_id": context.get("run_id"), "surface": initial_event["surface"],
                "purpose": initial_event["purpose"], "operation": operation,
                "request": request,
            })
        kwargs["timeout"] = _remaining_request_timeout(deadline)
        transport_attempted = True
        response = session.post(**kwargs)
        if response.status_code == 400 and re.search(
            r"maximum context length|context[_ ](?:length|window).*exceed|too many tokens",
            response.text, re.IGNORECASE,
        ):
            raise LLMContextWindowExceeded(response.text[:2000])
        if config.provider == "deepseek" and response.status_code == 402:
            key = _account_key(config)
            with _ACCOUNT_LOCK:
                _ACCOUNTS[key] = (time.monotonic(), True)
            _write_state("provider_account", key, {"blocked": True, "http_status": 402})
        return response
    except ProviderHTTPDeadlineExceeded as exc:
        error_type = 'LLMDeadlineExceeded'
        # This terminal deadline is not a retryable read-inactivity exception.
        # With no final response, the ordinary receipt retains unknown usage.
        raise LLMDeadlineExceeded(str(exc)) from exc
    except BaseException as exc:
        error_type = type(exc).__name__
        raise
    finally:
        data: dict[str, Any] = {}
        if response is not None:
            try:
                parsed = response.json()
                if isinstance(parsed, dict):
                    data = parsed
            except ValueError:
                pass
        event = {
            "attempt_id": attempt_id,
            "model_call_id": context.get("model_call_id"),
            "root_run_id": context.get("root_run_id") or os.getenv("PIPELINE_RUN_ID") or context.get("run_id"),
            "parent_run_id": context.get("run_id") or context.get("parent_run_id"),
            "settings_scope": llm_settings_scope(),
            "surface": context.get("surface") or getattr(client, "_zopedia_surface", "") or "provider.direct",
            "purpose": context.get("purpose") or "direct." + operation,
            "provider": config.provider, "requested_model": payload.get("model") or config.model,
            "reported_model": data.get("model"), "operation": operation,
            "status": "success" if response is not None and 200 <= response.status_code < 300 else "failure" if transport_attempted else "not_sent",
            "http_status": response.status_code if response is not None else None,
            "started_at_utc": started, "duration_ms": (time.monotonic() - t0) * 1000,
            "usage": data.get("usage"),
            "metadata": {
                **request_metadata,
                "transport_attempted": transport_attempted,
                "error_type": error_type, "provider_request_id": data.get("id"),
                # Preserve provider termination even when the client rejects an
                # empty or partial answer before a model output can be saved.
                "response_finish_reasons": [choice.get("finish_reason") for choice in
                    (data.get("choices") or []) if isinstance(choice, dict)],
                "research_subject": context.get("research_subject"),
                "artifact_input_id": context.get("artifact_input_id"),
                "literal_output": bool(context.get("literal_output")),
                # Configuration is not the wire contract: some providers omit
                # or ignore controls in reasoning mode. Record requested fields
                # only; an absent field is not an observed effective default.
                "input_characters": len(str(payload.get("text") or "")) if operation == "audio" else None,
                "estimated_charge": estimated_charge(provider=config.provider,
                    model=data.get("model") or (payload.get("model") if operation == "embedding" else ""),
                    usage=data.get("usage"), started_at=started),
            },
        }
        try:
            if not record_provider_attempt(event):
                log.warning("Provider attempt usage was not persisted: %s", event["attempt_id"])
        except Exception:
            # Central execution logs retain a recoverable metering receipt even
            # if both independent stores are unavailable. No request text or
            # credentials are included in this event.
            log.warning("Provider attempt accounting unavailable; receipt=%s",
                        json.dumps(event, sort_keys=True, default=str), exc_info=False)
        if local and local.retain_training_io and operation in {"chat", "json_repair"}:
            choices = []
            for choice in data.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message")
                if not isinstance(message, dict):
                    message = {}
                choices.append({
                    "index": choice.get("index"), "finish_reason": choice.get("finish_reason"),
                    "message": {name: message[name] for name in ("role", "content", "refusal", "tool_calls")
                                if name in message},
                })
            local.record("provider_outputs", {
                "attempt_id": attempt_id, "model_call_id": context.get("model_call_id"),
                "run_id": context.get("run_id"), "http_status": event["http_status"],
                "status": event["status"], "reported_model": data.get("model"),
                "usage": data.get("usage"),
                "choices": choices,
            })


def research_artifact_key(*, access_scope: str, subject: str, query: str,
                          schema: dict[str, Any], config: Any) -> tuple[str, str]:
    from .llm import get_active_narrative_style_rule, llm_settings_scope
    namespace = f"research_artifact:{llm_settings_scope()}:{access_scope}"
    from .aql_zopedia_engine import research_engine_revision
    contract = {"engine_revision": research_engine_revision(), "query": query, "schema": schema, "provider": config.provider,
                "endpoint": config.base_url, "model": config.model,
                "thinking": config.thinking_mode, "effort": execution_effort(config.reasoning_effort),
                "temperature": config.temperature, "style": get_active_narrative_style_rule(),
                "literal_output": preserves_literal_output(),
                "subject": subject, "representation_version": 1}
    if (local := current_local_execution()) and local.request_max_output_tokens is not None:
        contract["request_max_output_tokens"] = local.request_max_output_tokens
    key = hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return namespace, key


def read_research_artifact(namespace: str, key: str, *, strict: bool = False) -> dict[str, Any] | None:
    from .pipeline_store import read_execution_state
    read = (lambda ns, state_key: read_execution_state(ns, state_key, strict=True)) if strict else _read_state
    pointer = read(namespace + ":inputs", key)
    return read(namespace, pointer["artifact_id"]) if pointer else None


def retain_research_artifact(namespace: str, key: str, *, subject: str, task: str,
                             result: dict[str, Any]) -> dict[str, Any] | None:
    from .pipeline_store import retain_research_checkpoint
    artifact_id = hashlib.sha256((key + json.dumps(result, sort_keys=True, default=str)).encode()).hexdigest()
    value = {"artifact_id": artifact_id, "input_id": key, "subject": subject, "task": task,
             "created_at_utc": datetime.now(timezone.utc).isoformat(),
             "root_run_id": os.getenv("PIPELINE_RUN_ID"), "result": result}
    try:
        stored = retain_research_checkpoint(namespace=namespace, input_key=key, artifact=value,
                                            subject=subject, task=task)
        if stored:
            return {"artifact_id": artifact_id, "input_id": key, "namespace": namespace,
                    "created_at_utc": stored["created_at_utc"]}
    except Exception:
        log.warning("Completed research artifact was not checkpointed", exc_info=False)


def latest_research_artifact(*, access_scope: str, subject: str, task: str) -> dict[str, Any] | None:
    from .llm import llm_settings_scope
    namespace = f"research_artifact:{llm_settings_scope()}:{access_scope}"
    pointer = _read_state(namespace + ":latest", subject + ":" + task)
    artifact = _read_state(namespace, pointer["artifact_id"]) if pointer else None
    if not artifact:
        return None
    # Prior findings are evidence, not recursive copies of old agent prompts.
    return {key: artifact.get(key) for key in ("artifact_id", "input_id", "subject", "task", "created_at_utc")} | {
        "payload": (artifact.get("result") or {}).get("payload") or {},
        "artifact_context": (artifact.get("result") or {}).get("artifact_context") or {},
    }


def checkpoint_structured_research(function):
    """Opt-in, environment-owned replay of completed supplied-evidence stages.

    Live acquisition is never treated as timeless. Tool-using stages remain
    fresh research; their dated findings can be supplied to later planning.
    """
    @wraps(function)
    def run(**kwargs):
        from jsonschema import Draft202012Validator
        from .aql_zopedia_engine import load_aql_zopedia_llm_client

        access = kwargs.get("reuse_scope") or ""
        effort = kwargs.get("inference_effort") or ""
        subject = kwargs.get("research_subject") or ""
        def execute():
            result = function(**kwargs)
            if kwargs.get("artifact_context") is not None:
                result = {**result, "artifact_context": kwargs["artifact_context"]}
            return result
        with model_call_context(**{"research_subject": subject,
                                  **({"inference_effort": effort} if effort else {})}):
            if (not access or kwargs.get("persist_findings") or kwargs.get("conversation_history")
                    or kwargs.get("review_answer") or kwargs.get("max_tool_calls") is None):
                return execute()
            live_acquisition = kwargs["max_tool_calls"] > 0
            run_instance = os.getenv("CONTAINER_APP_JOB_EXECUTION_NAME") or os.getenv("PIPELINE_RUN_ID")
            if live_acquisition and not run_instance:
                return execute()
            client = kwargs.get("llm_client") or load_aql_zopedia_llm_client(
                surface=kwargs["surface"]
            )
            if client is None:
                return execute()
            kwargs["llm_client"] = client
            query = (kwargs.get("reuse_identity") or kwargs["query"]) + json.dumps(kwargs.get("initial_evidence"), sort_keys=True, default=str)
            query += json.dumps({key: kwargs.get(key) for key in (
                "instructions", "schema_name", "task", "surface", "write_policy", "request_context",
                "answer_review_owner", "run_timeout_seconds", "llm_step_timeout_seconds",
            )}, sort_keys=True, default=str)
            if kwargs.get("artifact_context") is not None:
                query += json.dumps(kwargs["artifact_context"], sort_keys=True, default=str)
            if live_acquisition:
                # Acquisition resumes only within this execution. A future
                # scheduled investigation must reassess fresh external inputs.
                query += json.dumps({"run_instance": run_instance, "max_tool_calls": kwargs["max_tool_calls"]})
            namespace, key = research_artifact_key(access_scope=access, subject=subject,
                                                  query=query, schema=kwargs["schema"], config=client.config)
            artifact = None if kwargs.get("force_refresh") else read_research_artifact(namespace, key)
            saved = (artifact or {}).get("result") or {}
            payload = saved.get("payload")
            if isinstance(payload, dict) and not list(Draft202012Validator(kwargs["schema"]).iter_errors(payload)):
                result = {**saved, "artifact_reuse": {"artifact_id": artifact["artifact_id"],
                                                     "created_at_utc": artifact["created_at_utc"],
                                                     "namespace": namespace, "input_id": key}}
                callback = kwargs.get("progress_callback")
                if callback:
                    callback({"stage": "research_artifact_reused", "message": "Reusing completed research for unchanged evidence.",
                              "artifact_id": artifact["artifact_id"], "progress": .9})
                return result
            incomplete = None if kwargs.get("force_refresh") else _read_state(namespace + ":incomplete", key)
            retained_agent = ((incomplete or {}).get("result") or {}).get("agent_result")
            if live_acquisition and isinstance(retained_agent, dict):
                kwargs["retained_agent_result"] = retained_agent
            partial_payload = ((incomplete or {}).get("result") or {}).get("partial_payload")
            if isinstance(partial_payload, dict):
                kwargs["retained_partial_payload"] = partial_payload
            partial_text = ((incomplete or {}).get("result") or {}).get("partial_text")
            if partial_text:
                kwargs["retained_partial_text"] = partial_text
            with model_call_context(artifact_input_id=key):
                result = execute()
            payload = result.get("payload")
            if (result.get("status") == "completed" and isinstance(payload, dict)
                    and not list(Draft202012Validator(kwargs["schema"]).iter_errors(payload))):
                reference = retain_research_artifact(namespace, key, subject=subject, task=kwargs["task"], result=result)
                if reference:
                    result = {**result, "research_artifact": reference}
            elif result.get("status") == "failed":
                # Keep paid work for bounded schema repair; never publish it as
                # a completed checkpoint or let it authorize knowledge writes.
                _write_state(namespace + ":incomplete", key, {
                    "input_id": key, "subject": subject, "task": kwargs["task"],
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "query": kwargs["query"], "schema": kwargs["schema"], "result": result,
                })
            return result
    return run


def checkpoint_agent_research(function):
    """Resume owned acquisition within one platform execution, never a new day.

    Knowledge write permission is separate from saving paid operational work.
    Private conversations remain with their existing user-owned run repository.
    """
    @wraps(function)
    def run(**kwargs):
        access = current_research_access_scope()
        execution = os.getenv("CONTAINER_APP_JOB_EXECUTION_NAME") or os.getenv("PIPELINE_RUN_ID")
        request_context = kwargs.get("request_context") or {}
        if (not access or not execution or kwargs.get("persist_findings", True)
                or kwargs.get("conversation_history") or kwargs.get("force_refresh")
                or kwargs.get("service") is not None
                or kwargs.get("write_policy") not in {None, "none"}
                or request_context.get("owner_id") or request_context.get("conversation_id")):
            return function(**kwargs)
        from . import llm, pipeline_store
        from .aql_zopedia_engine import (
            load_aql_zopedia_llm_client, research_engine_revision, resolve_research_budget,
        )
        # Import the shared acquisition owner before capturing its prompt defaults.
        from . import zopedia_agent, zopedia_research  # noqa: F401

        client = kwargs.get("llm_client") or load_aql_zopedia_llm_client(surface=kwargs.get("surface", "zopedia.chat"))
        if client is None:
            return function(**kwargs)
        kwargs["llm_client"] = client
        budget = resolve_research_budget(**{
            key: kwargs.get(key) for key in (
                "max_tool_calls", "run_timeout_seconds", "tool_call_timeout_seconds",
                "llm_step_timeout_seconds", "prefetch_timeout_seconds",
            )
        })
        llm.load_prompt_overrides()
        contract = {
            "execution": execution, "engine": research_engine_revision(),
            "request": {key: value for key, value in kwargs.items() if key not in {
                "llm_client", "service", "progress_callback", "checkpoint_callback", "resume_state",
            }},
            "budget": budget,
            "prompts": {item["key"]: item["prompt"] for item in llm.list_narrative_prompts()},
            "parameters": {item["key"]: item["value"] for item in llm.list_config_params()},
        }
        namespace, key = research_artifact_key(
            access_scope=access, subject=kwargs.get("task", "agent_answer"),
            query=json.dumps(contract, sort_keys=True, ensure_ascii=False, default=str),
            schema={}, config=client.config,
        )
        # A replica retry retains the research identity as well as its evidence.
        kwargs.setdefault("run_id", "agrun_" + key[:40])
        if kwargs["run_id"] is None:
            kwargs["run_id"] = "agrun_" + key[:40]
        owner = uuid.uuid4().hex
        lease_namespace = namespace + ":leases"
        deadline = time.monotonic() + float(budget["run_timeout_seconds"])
        claimed = False
        try:
            while True:
                artifact = read_research_artifact(namespace, key, strict=True)
                if artifact:
                    return {**artifact["result"], "artifact_reuse": {
                        "artifact_id": artifact["artifact_id"], "namespace": namespace,
                        "input_id": key, "created_at_utc": artifact["created_at_utc"],
                    }}
                claimed = pipeline_store.claim_execution_state(
                    lease_namespace, key, owner=owner,
                    seconds=float(budget["run_timeout_seconds"]) + 120,
                )
                if claimed:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Compatible research is still owned by another worker.")
                time.sleep(5)
            # Close the race between the preceding read and acquiring ownership.
            artifact = read_research_artifact(namespace, key, strict=True)
            if artifact:
                return {**artifact["result"], "artifact_reuse": {"artifact_id": artifact["artifact_id"], "input_id": key}}
            incomplete = pipeline_store.read_execution_state(namespace + ":incomplete", key, strict=True) or {}
            if incomplete.get("checkpoint") and not kwargs.get("resume_state"):
                state = incomplete["checkpoint"]
                kwargs["resume_state"] = state
                if not state.get("continued_request"):
                    # Restore defaults resolved on the first attempt, including
                    # its clock. Explicit inputs in this same contract still win.
                    kwargs["request_context"] = {
                        **(state.get("request_context") or {}),
                        **(kwargs.get("request_context") or {}),
                    }
            original_checkpoint = kwargs.get("checkpoint_callback")
            checkpoint_saved = bool(incomplete.get("checkpoint"))

            def checkpoint(state):
                nonlocal checkpoint_saved
                if original_checkpoint:
                    original_checkpoint(state)
                checkpoint_saved = pipeline_store.write_execution_state(namespace + ":incomplete", key, {
                    "checkpoint": state, "execution": execution, "input_id": key,
                })
                if not checkpoint_saved:
                    log.warning("Research acquisition checkpoint unavailable: %s", key)

            kwargs["checkpoint_callback"] = checkpoint
            with model_call_context(artifact_input_id=key):
                result = function(**kwargs)
            result.setdefault("runtime", {})["operational_checkpoint"] = checkpoint_saved
            result["runtime"]["resumable"] = checkpoint_saved or result["runtime"].get("resumable", False)
            if result.get("status") == "completed" and result.get("answer_markdown"):
                reference = retain_research_artifact(
                    namespace, key, subject="", task=kwargs.get("task", "agent_answer"), result=result,
                )
                if reference:
                    result = {**result, "research_artifact": reference}
            return result
        finally:
            if claimed:
                try:
                    pipeline_store.release_execution_state(lease_namespace, key, owner=owner)
                except Exception:
                    log.warning("Research execution lease release unavailable: %s", key)
    return run
