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


def probe(config, timeout: float = 3.0) -> LlmStatus:
    """Check whether the local endpoint is reachable and the model is pulled."""
    base_url = getattr(config, "llm_base_url", DEFAULT_BASE_URL).rstrip("/")
    model = getattr(config, "llm_model", "")
    if not getattr(config, "llm_enabled", False):
        return LlmStatus(False, base_url, model, detail="llm_enabled is false in config")
    try:
        raw = _http_get(f"{base_url}/api/tags", timeout=timeout)
    except LlmUnavailable as exc:
        return LlmStatus(False, base_url, model, detail=str(exc))

    try:
        names = tuple(str(m["name"]) for m in json.loads(raw).get("models", []))
    except (ValueError, KeyError, TypeError):
        names = ()

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


def _http_get(url: str, timeout: float) -> str:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise LlmUnavailable(f"cannot reach {url}: {getattr(exc, 'reason', exc)}") from exc
    except OSError as exc:
        raise LlmUnavailable(f"cannot reach {url}: {exc}") from exc


def _http_post(url: str, payload: dict, timeout: float) -> str:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise LlmUnavailable(f"model endpoint returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LlmUnavailable(f"cannot reach {url}: {getattr(exc, 'reason', exc)}") from exc
    except OSError as exc:
        raise LlmUnavailable(f"cannot reach {url}: {exc}") from exc
