"""LLM provider abstraction (spec §91; ADR 0009).

One :class:`Provider` protocol; three implementations:

* :class:`DeterministicProvider` — no network at all. Renders the
  privacy-filtered tool results through deterministic templates; every
  answer is labeled "deterministic summary (no LLM configured)". This is
  the DEFAULT when nothing is configured, so the coach works out of the
  box and never requires connectivity (ADR 0005 spirit).
* :class:`OllamaProvider` — local inference over Ollama's /api/chat
  (stdlib urllib in a worker thread; no vendor SDK). Enabled ONLY when
  OLLAMA_BASE_URL is set. Local provider: may receive ``local`` payloads.
* :class:`OpenAICompatibleProvider` — the external adapter. Refuses to
  even construct unless AI_ALLOW_EXTERNAL is true (ADR 0009 per-provider
  opt-in), and even then enforces the aggregates ceiling itself — a
  ``detailed``/``local`` payload raises before any bytes leave.

No core module imports a vendor SDK; HTTP is stdlib-only (spec §161).
"""

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Final, Protocol

from .privacy import LEVEL_RANK, PrivacyLevel
from .settings import Settings, get_settings

DETERMINISTIC_LABEL: Final[str] = "deterministic summary (no LLM configured)"

OLLAMA_SYSTEM_PROMPT: Final[str] = (
    "You are the Somatriq AI coach. You are given the user's question and "
    "deterministic tool results as JSON. Every number you mention MUST come "
    "from those results — never calculate, estimate or invent health data. "
    "Report data quality and caveats honestly; say when data is missing or "
    "insufficient. You do not diagnose. Answer concisely."
)

EXTERNAL_SYSTEM_PROMPT: Final[str] = OLLAMA_SYSTEM_PROMPT


class ProviderError(RuntimeError):
    """A provider call failed (transport, response shape, or bad prompt)."""


class ExternalProviderRefused(RuntimeError):
    """An external provider was configured without AI_ALLOW_EXTERNAL=true."""


class Provider(Protocol):
    """Provider-neutral completion surface (spec §91).

    Attributes are read-only properties so frozen dataclasses and plain
    test doubles can both satisfy the protocol.
    """

    @property
    def name(self) -> str: ...

    @property
    def is_local(self) -> bool: ...

    @property
    def max_privacy_level(self) -> PrivacyLevel: ...

    async def complete(self, prompt: str, *, privacy_level: PrivacyLevel) -> str:
        """Render an answer for the coach prompt (see coach.py for its JSON)."""
        ...  # pragma: no cover - protocol


# ── deterministic (default) ──────────────────────────────────────────────


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "no data yet"
    return f"{value:g}{suffix}" if isinstance(value, float) else f"{value}{suffix}"


def _today_line(result: dict[str, Any]) -> str:
    data = result.get("data") or {}
    recovery = data.get("recovery") or {}
    sleep = data.get("sleep") or {}
    hrv = data.get("hrv") or {}
    parts = [
        f"recovery {_fmt(recovery.get('score'), ' of 100')}",
        f"sleep {_fmt(sleep.get('duration_minutes'), ' min')}",
        f"HRV {_fmt(hrv.get('rmssd_ms'), ' ms')}",
        f"resting HR {_fmt(data.get('resting_hr'), ' bpm')}",
    ]
    return "Today: " + "; ".join(parts) + "."


def _baselines_line(result: dict[str, Any]) -> str:
    data = result.get("data") or {}
    if data.get("status") != "ok":
        return (
            f"{data.get('metric')} baseline: insufficient data "
            f"({data.get('days_available', 0)} of {data.get('required_minimum')} required days)."
        )
    return (
        f"{data.get('metric')} baseline: median {_fmt(data.get('median'))} "
        f"(IQR {_fmt(data.get('iqr'))}) over {data.get('n_days')} of "
        f"{data.get('window_days')} days."
    )


def _trends_line(result: dict[str, Any]) -> str:
    data = result.get("data") or {}
    return (
        f"{data.get('metric')} trend: {data.get('direction')} "
        f"({_fmt(data.get('slope_per_day'), ' per day')}, {data.get('n_points')} points)."
    )


def _journal_line(result: dict[str, Any]) -> str:
    events = (result.get("data") or {}).get("events") or []
    kinds = ", ".join(sorted({str(event.get("kind")) for event in events})) or "none"
    return f"Journal: {len(events)} events ({kinds})."


def _quality_line(result: dict[str, Any]) -> str:
    data = result.get("data") or {}
    days = data.get("days") or []
    insufficient = sum(1 for day in days if day.get("data_quality") == "insufficient")
    return (
        f"Data quality: mean coverage {result.get('quality', 0):.3f}; "
        f"{insufficient} of {len(days)} days insufficient."
    )


_TOOL_LINES: Final[dict[str, Any]] = {
    "get_today": _today_line,
    "get_baselines": _baselines_line,
    "get_trends": _trends_line,
    "get_journal": _journal_line,
    "get_data_quality": _quality_line,
}


def render_deterministic_answer(
    question: str, tool_results: dict[str, dict[str, Any]], *, privacy_level: PrivacyLevel
) -> str:
    """Honest template rendering of (already privacy-filtered) tool results.

    Deterministic: same inputs, same string. Every number shown comes from
    the filtered results, so the rendering can never reveal more than the
    active privacy level allows. A failed tool renders its error verbatim —
    degraded data is shown as degraded, never as healthy zeros (§158).
    """
    lines = [DETERMINISTIC_LABEL, f"Privacy level: {privacy_level}", "", f"Question: {question}"]
    for name, result in tool_results.items():
        data = result.get("data") or {}
        if data.get("error") is not None:
            lines.append(f"{name}: {data['error']}")
            continue
        renderer = _TOOL_LINES.get(name)
        if renderer is None:
            note = "withheld at this privacy level" if data.get("redacted") else "no data"
            lines.append(f"{name}: {note}")
        else:
            lines.append(renderer(result))
    return "\n".join(lines)


@dataclass(frozen=True)
class DeterministicProvider:
    """No network: the default provider when nothing is configured."""

    name: str = "deterministic"
    is_local: bool = True
    max_privacy_level: PrivacyLevel = "local"

    async def complete(self, prompt: str, *, privacy_level: PrivacyLevel) -> str:
        try:
            bundle: dict[str, Any] = json.loads(prompt)
            question = str(bundle.get("question") or "")
            results = bundle.get("tool_results") or {}
            if not isinstance(results, dict):
                raise TypeError("tool_results must be an object")
        except (ValueError, TypeError) as exc:
            msg = f"malformed coach prompt: {exc}"
            raise ProviderError(msg) from exc
        return render_deterministic_answer(question, results, privacy_level=privacy_level)


# ── shared stdlib HTTP ───────────────────────────────────────────────────


def _post_json(
    url: str, payload: dict[str, Any], *, timeout: float, headers: dict[str, str]
) -> dict[str, Any]:
    """Blocking JSON POST — called via asyncio.to_thread, never on the loop."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        msg = f"provider request to {url} failed: {exc}"
        raise ProviderError(msg) from exc
    if not isinstance(body, dict):
        msg = f"provider response from {url} is not a JSON object"
        raise ProviderError(msg)
    return body


async def _post_json_async(
    url: str, payload: dict[str, Any], *, timeout: float, headers: dict[str, str]
) -> dict[str, Any]:
    """Transport boundary: ANY failure under a provider call is a ProviderError."""
    try:
        return await asyncio.to_thread(_post_json, url, payload, timeout=timeout, headers=headers)
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - provider boundary maps all transport errors
        msg = f"provider request to {url} failed: {exc}"
        raise ProviderError(msg) from exc


def _checked_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if not base.startswith(("http://", "https://")):
        msg = f"provider base URL must be http(s), got {base_url!r}"
        raise ProviderError(msg)
    return base + path


# ── Ollama (local) ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class OllamaProvider:
    """Local inference via OLLAMA_BASE_URL/api/chat (enabled only when set)."""

    base_url: str
    model: str
    timeout_seconds: float = 60.0

    name: str = "ollama"
    is_local: bool = True
    max_privacy_level: PrivacyLevel = "local"

    async def complete(self, prompt: str, *, privacy_level: PrivacyLevel) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": OLLAMA_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        body = await _post_json_async(
            _checked_url(self.base_url, "/api/chat"),
            payload,
            timeout=self.timeout_seconds,
            headers={},
        )
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            msg = f"ollama response missing message.content: {sorted(body)}"
            raise ProviderError(msg)
        return content


# ── external (opt-in, aggregates ceiling) ────────────────────────────────


@dataclass(frozen=True)
class OpenAICompatibleProvider:
    """OpenAI-compatible chat completions — OFF unless AI_ALLOW_EXTERNAL=true.

    Even when enabled, this provider hard-refuses payloads above the
    ``aggregates`` privacy level (ADR 0009: external egress starts — and
    stays — at aggregates; ``detailed``/``local`` never leave).
    """

    base_url: str
    api_key: str
    model: str
    allow_external: bool

    name: str = "openai_compatible"
    is_local: bool = False
    max_privacy_level: PrivacyLevel = "aggregates"

    def __post_init__(self) -> None:
        if not self.allow_external:
            msg = (
                "external AI providers are disabled: set AI_ALLOW_EXTERNAL=true to "
                "opt in (ADR 0009 — per-provider opt-in, aggregates ceiling)"
            )
            raise ExternalProviderRefused(msg)

    async def complete(self, prompt: str, *, privacy_level: PrivacyLevel) -> str:
        if LEVEL_RANK[privacy_level] > LEVEL_RANK["aggregates"]:
            msg = (
                f"external provider refused a {privacy_level!r} payload: external "
                "egress is capped at 'aggregates' (ADR 0009)"
            )
            raise ProviderError(msg)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": EXTERNAL_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        body = await _post_json_async(
            _checked_url(self.base_url, "/chat/completions"),
            payload,
            timeout=60.0,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        choices = body.get("choices")
        message = choices[0].get("message") if isinstance(choices, list) and choices else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            msg = f"external response missing choices[0].message.content: {sorted(body)}"
            raise ProviderError(msg)
        return content


# ── selection ────────────────────────────────────────────────────────────


def build_provider(settings: Settings | None = None) -> Provider:
    """The provider selection matrix (ADR 0009):

    ============  =======================  =====================================
    configuration  provider                 ceiling
    ============  =======================  =====================================
    OLLAMA_BASE_URL  OllamaProvider         local (stays on the VPS)
    external URL, no opt-in  REFUSED (ExternalProviderRefused)
    external URL, AI_ALLOW_EXTERNAL  OpenAICompatible  aggregates (enforced)
    nothing set   DeterministicProvider    local (no network at all)
    ============  =======================  =====================================
    """
    active = settings or get_settings()
    if active.ollama_base_url:
        return OllamaProvider(
            base_url=active.ollama_base_url,
            model=active.ollama_model,
            timeout_seconds=active.ollama_timeout_seconds,
        )
    if active.external_base_url:
        return OpenAICompatibleProvider(
            base_url=active.external_base_url,
            api_key=active.external_api_key or "",
            model="gpt-4o-mini",
            allow_external=active.allow_external,
        )
    return DeterministicProvider()
