"""Phase 8: local LLM access.

FocusLedger talks to an Ollama-compatible HTTP endpoint on the loopback
interface. That keeps the whole feature local-first — the day's data never
leaves the machine — and costs no third-party dependency: this module is
stdlib-only.

The models this is aimed at are small (0.5B-3B). Everything downstream is built
on one rule: **a small model is asked to write prose, never to compute.** All
figures are calculated in Python and handed over already finished, so a weak
model cannot produce wrong numbers — only weaker wording.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:11434"

# Small local models people actually run, best-first. Used only for hints.
SUGGESTED_MODELS = ("qwen2.5:1.5b", "qwen2.5:3b", "qwen3:1.7b", "llama3.2:1b", "gemma2:2b")


class LlmUnavailable(RuntimeError):
    """The local model endpoint could not be used. Callers must degrade."""


@dataclass(frozen=True)
class LlmStatus:
    available: bool
    base_url: str
    model: str
    installed_models: tuple[str, ...] = ()
    detail: str = ""

    @property
    def model_installed(self) -> bool:
        return any(_same_model(m, self.model) for m in self.installed_models)


def _same_model(candidate: str, wanted: str) -> bool:
    """Ollama reports ``qwen2.5:1.5b``; treat a bare name as ``name:latest``."""
    candidate, wanted = candidate.strip(), wanted.strip()
    if candidate == wanted:
        return True
    return candidate.split(":")[0] == wanted.split(":")[0] and ":" not in wanted


def installed_models(config, timeout: float = 3.0) -> tuple[str, ...]:
    """Names of the models pulled into the local endpoint."""
    base_url = getattr(config, "llm_base_url", DEFAULT_BASE_URL).rstrip("/")
    raw = _http_get(f"{base_url}/api/tags", timeout=timeout)
    try:
        return tuple(str(m["name"]) for m in json.loads(raw).get("models", []))
    except (ValueError, KeyError, TypeError):
        return ()


def probe(config, timeout: float = 3.0) -> LlmStatus:
    """Check whether the local endpoint is reachable and the model is pulled."""
    base_url = getattr(config, "llm_base_url", DEFAULT_BASE_URL).rstrip("/")
    model = getattr(config, "llm_model", "")
    if not getattr(config, "llm_enabled", False):
        return LlmStatus(False, base_url, model, detail="llm_enabled is false in config")
    try:
        names = installed_models(config, timeout=timeout)
    except LlmUnavailable as exc:
        return LlmStatus(False, base_url, model, detail=str(exc))

    status = LlmStatus(True, base_url, model, names)
    if not status.model_installed:
        return LlmStatus(
            False, base_url, model, names,
            detail=f"model '{model}' is not pulled (run: ollama pull {model})",
        )
    return status


def generate(config, system: str, user: str, timeout: float | None = None,
             temperature: float = 0.3, max_tokens: int = 900) -> str:
    """Ask the local model for a completion. Raises ``LlmUnavailable`` on failure."""
    base_url = getattr(config, "llm_base_url", DEFAULT_BASE_URL).rstrip("/")
    model = getattr(config, "llm_model", "")
    if not getattr(config, "llm_enabled", False):
        raise LlmUnavailable("llm_enabled is false in config")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    body = _http_post(
        f"{base_url}/api/chat",
        payload,
        timeout=timeout or float(getattr(config, "llm_timeout_seconds", 120)),
    )
    try:
        parsed = json.loads(body)
    except ValueError as exc:
        raise LlmUnavailable(f"model returned invalid JSON: {exc}") from exc

    message = parsed.get("message") or {}
    text = str(message.get("content") or "").strip()
    if not text:
        raise LlmUnavailable("model returned an empty response")
    return text


def chat(
    config,
    messages: list[dict[str, object]],
    *,
    model: str | None = None,
    tools: list[dict[str, object]] | None = None,
    think: bool | None = None,
    num_ctx: int | None = None,
    temperature: float = 0.2,
    timeout: float | None = None,
) -> dict[str, object]:
    """One raw ``/api/chat`` turn, tools included; returns Ollama's whole reply.

    Unlike ``generate`` the caller keeps the conversation, so a model can call
    tools across several turns. Raises ``LlmUnavailable`` on failure.
    """
    base_url = getattr(config, "llm_base_url", DEFAULT_BASE_URL).rstrip("/")
    if not getattr(config, "llm_enabled", False):
        raise LlmUnavailable("llm_enabled is false in config")

    options: dict[str, object] = {"temperature": temperature}
    if num_ctx:
        options["num_ctx"] = num_ctx
    payload: dict[str, object] = {
        "model": model or getattr(config, "llm_model", ""),
        "messages": messages,
        "stream": False,
        "options": options,
    }
    if tools:
        payload["tools"] = tools
    if think is not None:
        payload["think"] = think
    body = _http_post(
        f"{base_url}/api/chat",
        payload,
        timeout=timeout or float(getattr(config, "llm_timeout_seconds", 120)),
    )
    try:
        parsed = json.loads(body)
    except ValueError as exc:
        raise LlmUnavailable(f"model returned invalid JSON: {exc}") from exc
    if not isinstance(parsed.get("message"), dict):
        raise LlmUnavailable("model reply has no message")
    return parsed


def model_capabilities(config, model: str, timeout: float = 10.0) -> tuple[str, ...]:
    """What a pulled model supports, e.g. ``("completion", "tools", "thinking")``."""
    base_url = getattr(config, "llm_base_url", DEFAULT_BASE_URL).rstrip("/")
    body = _http_post(f"{base_url}/api/show", {"model": model}, timeout=timeout)
    try:
        return tuple(str(c) for c in json.loads(body).get("capabilities") or ())
    except (ValueError, AttributeError) as exc:
        raise LlmUnavailable(f"cannot read capabilities of {model}: {exc}") from exc


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def is_loopback(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host in LOOPBACK_HOSTS or host.startswith("127.")


def _opener(url: str):
    """Build an opener that never proxies a request to the local machine.

    urllib honours ``http_proxy`` for every request, so on a machine behind a
    corporate or campus proxy even ``http://127.0.0.1:11434`` gets handed to
    the proxy, which refuses it with a 403. A model running on this machine
    must always be reached directly.
    """
    if is_loopback(url):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _describe(exc: BaseException) -> str:
    reason = getattr(exc, "reason", exc)
    return str(reason) or exc.__class__.__name__


def _http_get(url: str, timeout: float) -> str:
    request = urllib.request.Request(url, method="GET")
    try:
        with _opener(url).open(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise LlmUnavailable(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise LlmUnavailable(f"cannot reach {url}: {_describe(exc)}") from exc
    except OSError as exc:
        raise LlmUnavailable(f"cannot reach {url}: {exc}") from exc


def _http_post(url: str, payload: dict, timeout: float) -> str:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with _opener(url).open(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()[:200]
        raise LlmUnavailable(f"model endpoint returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LlmUnavailable(f"cannot reach {url}: {_describe(exc)}") from exc
    except OSError as exc:
        raise LlmUnavailable(f"cannot reach {url}: {exc}") from exc
