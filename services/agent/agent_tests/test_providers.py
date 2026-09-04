"""Provider matrix + transport tests (spec §91; ADR 0009).

All network I/O is intercepted: providers are tested against a stubbed
_post_json so nothing ever leaves 127.0.0.1-free test processes.
"""

import json
import urllib.error
from typing import Any

import pytest
from somatriq_agent.privacy import PrivacyLevel
from somatriq_agent.providers import (
    DETERMINISTIC_LABEL,
    DeterministicProvider,
    ExternalProviderRefused,
    OllamaProvider,
    OpenAICompatibleProvider,
    ProviderError,
    build_provider,
    render_deterministic_answer,
)
from somatriq_agent.settings import get_settings

# ── selection matrix ─────────────────────────────────────────────────────


def _provider_name(monkeypatch: pytest.MonkeyPatch, **env: str) -> str:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    return build_provider().name


def test_matrix_default_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _provider_name(monkeypatch) == "deterministic"


def test_matrix_ollama_enabled_only_by_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = build_provider()  # baseline: default
    assert isinstance(provider, DeterministicProvider)
    name = _provider_name(monkeypatch, OLLAMA_BASE_URL="http://127.0.0.1:11434")
    assert name == "ollama"


def test_matrix_external_refused_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "sk-test")
    get_settings.cache_clear()
    with pytest.raises(ExternalProviderRefused, match="AI_ALLOW_EXTERNAL"):
        build_provider()


def test_matrix_external_enabled_with_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    name = _provider_name(
        monkeypatch,
        OPENAI_COMPAT_BASE_URL="https://api.example.com/v1",
        OPENAI_COMPAT_API_KEY="sk-test",
        AI_ALLOW_EXTERNAL="true",
    )
    assert name == "openai_compatible"


def test_matrix_allow_external_alone_stays_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The flag is an opt-in, never an enabler by itself (ADR 0009).
    assert _provider_name(monkeypatch, AI_ALLOW_EXTERNAL="true") == "deterministic"


def test_matrix_ollama_wins_over_external(monkeypatch: pytest.MonkeyPatch) -> None:
    name = _provider_name(
        monkeypatch,
        OLLAMA_BASE_URL="http://127.0.0.1:11434",
        OPENAI_COMPAT_BASE_URL="https://api.example.com/v1",
        AI_ALLOW_EXTERNAL="true",
    )
    assert name == "ollama"  # local beats external whenever available


def test_settings_reject_unknown_privacy_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PRIVACY_LEVEL", "everything")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="AI_PRIVACY_LEVEL"):
        get_settings()
    get_settings.cache_clear()


# ── deterministic provider ───────────────────────────────────────────────


def _bundle(question: str, tool_results: dict[str, Any]) -> str:
    return json.dumps({"question": question, "tool_results": tool_results})


async def test_deterministic_complete_renders_tool_results() -> None:
    provider = DeterministicProvider()
    prompt = _bundle(
        "How is my recovery today?",
        {
            "get_today": {
                "data": {
                    "recovery": {"score": 76.66},
                    "sleep": {"duration_minutes": 65.0},
                    "hrv": {"rmssd_ms": 105.0},
                    "resting_hr": 60.0,
                },
                "caveats": ["sample caveat"],
            }
        },
    )

    answer = await provider.complete(prompt, privacy_level="local")

    assert answer.startswith(DETERMINISTIC_LABEL)
    assert "Question: How is my recovery today?" in answer
    assert "recovery 76.66 of 100" in answer
    assert "sleep 65 min" in answer
    assert "HRV 105 ms" in answer
    assert "resting HR 60 bpm" in answer
    assert "sample caveat" not in answer  # caveats ride the envelope, not the line


async def test_deterministic_complete_rejects_malformed_prompt() -> None:
    provider = DeterministicProvider()
    with pytest.raises(ProviderError, match="malformed coach prompt"):
        await provider.complete("not json", privacy_level="local")


def test_render_deterministic_answer_handles_insufficient_data() -> None:
    answer = render_deterministic_answer(
        "baseline?",
        {
            "get_baselines": {
                "data": {
                    "metric": "resting_hr",
                    "status": "insufficient data",
                    "days_available": 3,
                    "required_minimum": 7,
                }
            }
        },
        privacy_level="local",
    )
    assert "insufficient data (3 of 7 required days)" in answer


def test_render_deterministic_answer_marks_redacted_tools() -> None:
    answer = render_deterministic_answer(
        "?", {"get_heart_rate_summary": {"data": {"redacted": True}}}, privacy_level="aggregates"
    )
    assert "withheld at this privacy level" in answer


# ── ollama provider ──────────────────────────────────────────────────────


class _StubTransport:
    """Records _post_json calls; returns a canned body or raises."""

    def __init__(self, body: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.body = body or {}
        self.error = error

    def __call__(self, url: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"url": url, "payload": payload, **kwargs})
        if self.error is not None:
            raise self.error
        return self.body


async def test_ollama_complete_posts_chat_api(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubTransport(body={"message": {"content": "coach says hi"}})
    monkeypatch.setattr("somatriq_agent.providers._post_json", stub)
    provider = OllamaProvider(base_url="http://127.0.0.1:11434/", model="llama3.1")

    answer = await provider.complete(_bundle("q", {}), privacy_level="local")

    assert answer == "coach says hi"
    call = stub.calls[0]
    assert call["url"] == "http://127.0.0.1:11434/api/chat"
    system, user = call["payload"]["messages"]
    assert system["role"] == "system"
    assert "never calculate" in system["content"]
    assert json.loads(user["content"]) == {"question": "q", "tool_results": {}}
    assert call["payload"]["stream"] is False


async def test_ollama_complete_wraps_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubTransport(error=urllib.error.URLError("connection refused"))
    monkeypatch.setattr("somatriq_agent.providers._post_json", stub)
    provider = OllamaProvider(base_url="http://127.0.0.1:11434", model="m")

    with pytest.raises(ProviderError, match="provider request"):
        await provider.complete(_bundle("q", {}), privacy_level="local")


async def test_ollama_complete_rejects_shapeless_response(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubTransport(body={"done": True})
    monkeypatch.setattr("somatriq_agent.providers._post_json", stub)
    provider = OllamaProvider(base_url="http://127.0.0.1:11434", model="m")

    with pytest.raises(ProviderError, match="message.content"):
        await provider.complete(_bundle("q", {}), privacy_level="local")


# ── external provider ────────────────────────────────────────────────────


def test_external_provider_refuses_construction_without_opt_in() -> None:
    with pytest.raises(ExternalProviderRefused):
        OpenAICompatibleProvider(
            base_url="https://api.example.com/v1", api_key="sk", model="m", allow_external=False
        )


async def test_external_provider_refuses_above_aggregates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubTransport()
    monkeypatch.setattr("somatriq_agent.providers._post_json", stub)
    provider = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1", api_key="sk", model="m", allow_external=True
    )

    levels: tuple[PrivacyLevel, ...] = ("detailed", "local")
    for level in levels:
        with pytest.raises(ProviderError, match="capped at 'aggregates'"):
            await provider.complete(_bundle("q", {}), privacy_level=level)
    assert stub.calls == []  # refused before any bytes leave


async def test_external_provider_works_at_aggregates(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubTransport(
        body={"choices": [{"message": {"role": "assistant", "content": "external answer"}}]}
    )
    monkeypatch.setattr("somatriq_agent.providers._post_json", stub)
    provider = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1/", api_key="sk-test", model="m", allow_external=True
    )

    answer = await provider.complete(_bundle("q", {}), privacy_level="aggregates")

    assert answer == "external answer"
    call = stub.calls[0]
    assert call["url"] == "https://api.example.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
