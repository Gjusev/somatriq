"""Somatriq agent service: the AI coach engine (M8, spec §89-94; ADR 0009).

Division of labor (ADR 0009): all numeric results come from the
deterministic tool layer (somatriq_agent_tools) and reach a provider only
through the privacy chokepoint (``somatriq_agent.privacy.apply_privacy``).
The LLM narrates; it never computes.
"""

from .coach import (
    CoachAnswer,
    CoachEngine,
    ToolCall,
    build_engine,
    reset_engine_cache,
    select_tools,
)
from .privacy import (
    DEFAULT_PRIVACY_LEVEL,
    LEVEL_RANK,
    PRIVACY_LEVELS,
    PrivacyLevel,
    apply_privacy,
    clamp_level,
)
from .providers import (
    DETERMINISTIC_LABEL,
    DeterministicProvider,
    ExternalProviderRefused,
    OllamaProvider,
    OpenAICompatibleProvider,
    Provider,
    ProviderError,
    build_provider,
)
from .settings import Settings, get_settings

__all__ = [
    "DEFAULT_PRIVACY_LEVEL",
    "DETERMINISTIC_LABEL",
    "LEVEL_RANK",
    "PRIVACY_LEVELS",
    "CoachAnswer",
    "CoachEngine",
    "DeterministicProvider",
    "ExternalProviderRefused",
    "OpenAICompatibleProvider",
    "OllamaProvider",
    "PrivacyLevel",
    "Provider",
    "ProviderError",
    "Settings",
    "ToolCall",
    "apply_privacy",
    "build_engine",
    "build_provider",
    "clamp_level",
    "get_settings",
    "reset_engine_cache",
    "select_tools",
]
