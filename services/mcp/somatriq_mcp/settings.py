"""MCP service settings from environment (spec §209; mirrors somatriq_api).

Stdlib-only, frozen dataclass + lru_cache — same shape as the api settings so
the two services stay operationally symmetric (spec §161 dependency policy).
"""

import os
from dataclasses import dataclass
from functools import lru_cache

DEFAULT_USER_TIMEZONE = "UTC"
DEFAULT_PORT = 8100
DEFAULT_LOG_LEVEL = "INFO"


@dataclass(frozen=True)
class Settings:
    somatriq_env: str
    user_timezone: str
    port: int
    log_level: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        somatriq_env=os.environ.get("SOMATRIQ_ENV", "development"),
        user_timezone=os.environ.get("USER_TIMEZONE", DEFAULT_USER_TIMEZONE),
        # Historical name from the M0 stub, kept so compose stays untouched.
        port=int(os.environ.get("MCP_HEALTH_PORT", str(DEFAULT_PORT))),
        log_level=os.environ.get("LOG_LEVEL", DEFAULT_LOG_LEVEL),
    )
