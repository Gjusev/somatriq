"""Process settings from environment (spec §209).

Stdlib-only for M1; grow into typed settings only when the variable count
justifies a dependency (spec §161 dependency policy).
"""

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    somatriq_env: str
    ingest_token: str | None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        somatriq_env=os.environ.get("SOMATRIQ_ENV", "development"),
        ingest_token=os.environ.get("INGEST_TOKEN") or None,
    )
