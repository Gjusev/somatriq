"""Raw frame transport contracts (spec §47-49, ADR 0003).

M2 transmits raw frames and decoded observations together, from day one.
The server treats the raw payload as an opaque compressed blob until the
M4 replay tooling exists; the container format below is produced by the
collector's pre-decoder capture point and consumed again only by replay.

Somatriq Raw Journal v1 (the bytes inside ``payload_b64`` after zstd
depression — defined here, owned by the collector and M4 replay):

    entry   := u32_be length N | u64_be epoch_ms | N bytes frame
    journal := zstd( entry* )

- ``length`` counts only the frame bytes (not the 12-byte header).
- ``epoch_ms`` is the collector clock at capture, UTC.
- a frame is one complete reconstructed BLE payload as handed to the
  NOOP decoder — the capture point must never see partial reads.
"""

import base64
import binascii
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# 8 MiB decoded cap: a 5-minute sync window is a few KB compressed; the cap
# exists to reject runaway payloads before they reach storage.
RAW_PAYLOAD_MAX_BYTES = 8 * 1024 * 1024
# base64 inflates by 4/3 plus padding; +16 headroom for the padding chars.
_RAW_PAYLOAD_B64_MAX_CHARS = (RAW_PAYLOAD_MAX_BYTES // 3 + 1) * 4 + 16

RAW_CODEC_ZSTD: Literal["zstd"] = "zstd"
RAW_JOURNAL_VERSION = 1


class RawPayload(BaseModel):
    """Opaque zstd journal segment accompanying a batch (schema v2 only)."""

    codec: Literal["zstd"] = RAW_CODEC_ZSTD
    journal_version: int = Field(default=RAW_JOURNAL_VERSION, ge=1, le=1)
    frame_count: int = Field(ge=1)
    payload_b64: str = Field(min_length=4, max_length=_RAW_PAYLOAD_B64_MAX_CHARS)
    payload_sha256: str = Field(min_length=64, max_length=64)
    uncompressed_bytes: int = Field(ge=1)
    first_frame_ts: str | None = None  # ISO-8601; diagnostics only
    last_frame_ts: str | None = None  # ISO-8601; diagnostics only

    @field_validator("payload_b64")
    @classmethod
    def _valid_base64(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            msg = "payload_b64 is not valid base64"
            raise ValueError(msg) from exc
        if len(decoded) > RAW_PAYLOAD_MAX_BYTES:
            msg = f"decoded raw payload exceeds {RAW_PAYLOAD_MAX_BYTES} bytes"
            raise ValueError(msg) from None
        return value

    def decoded_bytes(self) -> bytes:
        """The opaque compressed journal segment (server never decompresses)."""
        return base64.b64decode(self.payload_b64, validate=True)
