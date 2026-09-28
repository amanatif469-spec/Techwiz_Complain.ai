from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

import httpx

from config.settings import CHAT_PROVIDERS, Settings, get_settings
from python_validation.schema import coerce_enums, extract_json, structural_errors

logger = logging.getLogger("supportnova.genai")

# Retrying these buys nothing: the key, route, or permission is wrong until a human fixes it.
PERMANENT_STATUS = {400, 401, 403, 404, 405}


class GenAIError(RuntimeError):
    def __init__(self, message: str, *, permanent: bool = False, attempts: int = 0) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.attempts = attempts


def provider_chain(settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    names = [settings.genai_provider.lower(), *settings.genai_fallback_list]
    if getattr(settings, "genai_auto_fallback", True):
        names.extend(CHAT_PROVIDERS)
    ordered: list[str] = []
    for name in names:
        alias = "grok" if name == "xai" else name
        if alias not in ordered and settings.provider_has_key(alias):
            ordered.append(alias)
    return ordered


def generate_structured(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    settings = get_settings()
    configured = provider_chain(settings)
    if not configured:
        raise GenAIError("No GenAI provider API key is configured.")
    chain = [p for p in configured if not _cooling_down(p)]
    paused = [p for p in configured if p not in chain]
    if not chain:
        raise GenAIError(
            "All GenAI providers are paused after permanent errors (credits, key or permission): "
            + ", ".join(paused)
        )

    errors: list[str] = [f"{p}: paused after a permanent error" for p in paused]
    attempt_log: list[dict[str, Any]] = []
    deadline = time.perf_counter() + settings.genai_total_budget_seconds
    for provider in chain:
        provider_error = "unknown error"
        for attempt in range(1, settings.genai_max_retries + 1):
            remaining = deadline - time.perf_counter()
            if remaining < 1:
                provider_error = f"time budget of {settings.genai_total_budget_seconds}s exhausted"
                break
            started = time.perf_counter()
            try:
                raw = _call_with_deadline(provider, system_prompt, user_prompt, settings, remaining)
                parsed = coerce_enums(extract_json(raw))
                # Incomplete output (missing required fields, wrong types) is retried like a
                # transport error rather than passed downstream as if it were usable.
                problems = structural_errors(parsed)
                if problems:
                    raise InvalidOutputError("Schema-invalid output: " + "; ".join(problems[:5]))
                attempt_log.append({"provider": provider, "attempt": attempt, "ok": True})
                return {
                    "raw": raw,
                    "structured": parsed,
                    "attempt": attempt,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "provider": provider,
                    "model": _model_name(provider, settings),
                    "error": "",
                    "fallback_used": provider != chain[0],
                    "providers_tried": chain[: chain.index(provider) + 1],
                    "attempt_log": attempt_log,
                }
            except GenAIError as exc:
                provider_error = str(exc)
                _record_failure(attempt_log, provider, attempt, provider_error)
                if exc.permanent:
                    _pause(provider, provider_error)
                    break
            except Exception as exc:  # noqa: BLE001 — retry this provider, then fall back
                provider_error = str(exc) or exc.__class__.__name__
                _record_failure(attempt_log, provider, attempt, provider_error)
            if attempt < settings.genai_max_retries:
                time.sleep(max(0.0, min(1.5 * attempt, 3, deadline - time.perf_counter() - 1)))
        errors.append(f"{provider}: {provider_error}")
    raise GenAIError("All GenAI providers failed — " + " | ".join(errors), attempts=len(attempt_log))


class InvalidOutputError(ValueError):
    """The model answered, but not with a usable structured result."""


# httpx timeouts apply per network phase, so a slow response can outlive them. Running the
# call in a worker gives a true wall-clock cap; an abandoned call finishes in the background.
_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="genai")
# Providers that failed permanently (no credits, bad key) are skipped for a while instead of
# costing every analysis a round trip.
PERMANENT_COOLDOWN_SECONDS = 600
_paused_until: dict[str, float] = {}


def _call_with_deadline(provider: str, system_prompt: str, user_prompt: str, settings: Settings, remaining: float) -> str:
    timeout = max(1.0, min(settings.genai_timeout_seconds, remaining))
    future = _EXECUTOR.submit(_dispatch, provider, system_prompt, user_prompt, settings, timeout)
    try:
        return future.result(timeout=timeout + 0.5)
    except FutureTimeout as exc:
        future.cancel()
        raise TimeoutError(f"no response within {timeout:.1f}s") from exc


def _cooling_down(provider: str) -> bool:
    until = _paused_until.get(provider)
    if until and until > time.monotonic():
        return True
    _paused_until.pop(provider, None)
    return False


def _pause(provider: str, reason: str) -> None:
    _paused_until[provider] = time.monotonic() + PERMANENT_COOLDOWN_SECONDS
    logger.warning("Pausing GenAI provider %s for %ss: %s", provider, PERMANENT_COOLDOWN_SECONDS, reason[:200])


def paused_providers() -> dict[str, int]:
    now = time.monotonic()
    return {p: int(until - now) for p, until in _paused_until.items() if until > now}


def reset_provider_pauses() -> None:
    """Clear cooldowns, e.g. after an administrator tops up credits or fixes a key."""
    _paused_until.clear()


def _record_failure(log: list[dict[str, Any]], provider: str, attempt: int, error: str) -> None:
    log.append({"provider": provider, "attempt": attempt, "ok": False, "error": error[:300]})
    logger.warning("GenAI attempt failed provider=%s attempt=%s error=%s", provider, attempt, error[:300])


def _model_name(provider: str, settings: Settings) -> str:
    return {
        "gemini": settings.gemini_model,
        "anthropic": settings.anthropic_model,
        "grok": settings.grok_model,
        "xai": settings.grok_model,
        "openai": settings.openai_model,
    }.get(provider, settings.openai_model)


def _dispatch(provider: str, system_prompt: str, user_prompt: str, settings: Settings, timeout: float) -> str:
    if provider == "gemini":
        return _gemini(system_prompt, user_prompt, settings, timeout)
    if provider == "anthropic":
        return _anthropic(system_prompt, user_prompt, settings, timeout)
    if provider in {"grok", "xai"}:
        return _grok(system_prompt, user_prompt, settings, timeout)
    return _openai(system_prompt, user_prompt, settings, timeout)


def _openai_compatible(
    *,
    url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout: float,
    extra_headers: dict[str, str] | None = None,
    json_mode: bool = True,
) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        **(extra_headers or {}),
    }
    body: dict[str, Any] = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, headers=headers, json=body)
        # Only drop JSON mode when the model actually rejected response_format; retrying
        # blindly on every 4xx hid the real cause (bad key, missing route) behind a second failure.
        if json_mode and response.status_code == 400 and "response_format" in response.text:
            return _openai_compatible(
                url=url,
                api_key=api_key,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                timeout=timeout,
                extra_headers=extra_headers,
                json_mode=False,
            )
        if response.status_code >= 400:
            raise GenAIError(_http_error(response), permanent=_is_permanent(response))
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            return "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return content or ""


def _http_error(response: httpx.Response) -> str:
    # The URL can carry the API key as a query param; this message is persisted on the
    # GenAIRun row and shown to reviewers, so it must never leak a credential.
    url = re.sub(r"(key|api_key|access_token)=[^&]+", r"\1=REDACTED", str(response.request.url))
    return f"HTTP {response.status_code} {url}: {response.text[:500]}"


def _is_permanent(response: httpx.Response) -> bool:
    if response.status_code in PERMANENT_STATUS:
        return True
    # A 429 is normally worth retrying, but an exhausted balance will not refill mid-run.
    return response.status_code == 429 and any(
        marker in response.text.lower()
        for marker in ("insufficient_quota", "credit_balance_exhausted", "billing", "no credits")
    )


def _openai(system_prompt: str, user_prompt: str, settings: Settings, timeout: float) -> str:
    if not settings.openai_api_key:
        raise GenAIError("OPENAI_API_KEY is not configured.")
    return _openai_compatible(
        url="https://api.openai.com/v1/chat/completions",
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        timeout=timeout,
    )


def _grok(system_prompt: str, user_prompt: str, settings: Settings, timeout: float) -> str:
    key = settings.grok_key
    if not key:
        raise GenAIError("GROK_API_KEY / XAI_API_KEY is not configured.")
    return _openai_compatible(
        url="https://api.x.ai/v1/chat/completions",
        api_key=key,
        model=settings.grok_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        timeout=timeout,
    )


def _gemini(system_prompt: str, user_prompt: str, settings: Settings, timeout: float) -> str:
    if not settings.gemini_api_key:
        raise GenAIError("GEMINI_API_KEY is not configured.")
    # Key travels as a header, not a query param, so it cannot leak through error text or logs.
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
    headers = {"x-goog-api-key": settings.gemini_api_key, "Content-Type": "application/json"}
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, headers=headers, json=body)
        if response.status_code >= 400:
            raise GenAIError(_http_error(response), permanent=_is_permanent(response))
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


def _anthropic(system_prompt: str, user_prompt: str, settings: Settings, timeout: float) -> str:
    if not settings.anthropic_api_key:
        raise GenAIError("ANTHROPIC_API_KEY is not configured.")
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": settings.anthropic_model,
        "max_tokens": 2000,
        "temperature": 0.2,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    with httpx.Client(timeout=timeout) as client:
        response = client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
        if response.status_code >= 400:
            raise GenAIError(_http_error(response), permanent=_is_permanent(response))
        data = response.json()
        return "".join(part.get("text", "") for part in data.get("content", []))
