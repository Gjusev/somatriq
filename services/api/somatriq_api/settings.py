"""Process settings from environment (spec §209).

Stdlib-only for M1; grow into typed settings only when the variable count
justifies a dependency (spec §161 dependency policy).
"""

import os
from dataclasses import dataclass
from functools import lru_cache

DEFAULT_RAW_DIR = "/var/lib/somatriq/raw"


@dataclass(frozen=True)
class Settings:
    somatriq_env: str
    ingest_token: str | None
    raw_dir: str
    secret_key: str | None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        somatriq_env=os.environ.get("SOMATRIQ_ENV", "development"),
        ingest_token=os.environ.get("INGEST_TOKEN") or None,
        raw_dir=os.environ.get("SOMATRIQ_RAW_DIR", DEFAULT_RAW_DIR),
        secret_key=os.environ.get("SECRET_KEY") or None,
    )
