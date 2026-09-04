"""Pluggable frame decoders for replay reprocessing (ADR 0004/0012).

Decoders turn archived frames back into observations and write them ONLY as
parallel versions (timeseries.replayed_observations) — the live hypertable
is the ingest-time interpretation and is never rewritten (ADR 0012).

The WHOOP 4.0 realtime HR decoder below is built from documented protocol
FACTS only (NOOP's PolyForm-NC license expressly declares frame layouts,
offsets and dtypes uncopyrightable; NOOP code is not copied — spec §29).
Table source: whoop_protocol.json (johnmiddleton12/my-whoop lineage).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from somatriq_replay.journal import Frame

# ── documented WHOOP 4.0 realtime facts (whoop_protocol.json) ────────────
# envelope:  SOF u8@0 | length u16@1 | crc8 u8@3 | packet_type u8@4 | seq u8@5
# REALTIME_DATA (packet_type 40) payload, little-endian:
#   timestamp u32@6 (unix seconds) | subseconds u16@10 (1/32768 s) |
#   heart_rate u8@12 (bpm)        | rr_count u8@13
WHOOP4_PACKET_TYPE_OFFSET = 4
WHOOP4_REALTIME_TYPE = 40
WHOOP4_TS_OFFSET = 6
WHOOP4_SUBSEC_OFFSET = 10
WHOOP4_HR_OFFSET = 12
WHOOP4_MIN_FRAME = 14  # envelope header (6) + through heart_rate (12+1)
SUBSECOND_DENOMINATOR = 32768

WHOOP4_REALTIME_HR_V1 = "whoop4-realtime-hr/v1"


@dataclass(frozen=True)
class DecodedObservation:
    """One observation re-derived from an archived frame."""

    ts: datetime
    bpm: float
    source_frame_epoch_ms: int

    @property
    def source_record_id(self) -> str:
        return f"replay-frame-{self.source_frame_epoch_ms}"


class FrameDecoder(Protocol):
    """A replay decoder. Versioned name; output is always parallel-versioned."""

    @property
    def decoder_version(self) -> str: ...

    def decode(self, frame: Frame) -> DecodedObservation | None: ...


class Whoop4RealtimeHrDecoder:
    """WHOOP 4.0 REALTIME_DATA heart rate, from documented frame facts."""

    def __init__(self, version: str = WHOOP4_REALTIME_HR_V1) -> None:
        self._version = version

    @property
    def decoder_version(self) -> str:
        return self._version

    def decode(self, frame: Frame) -> DecodedObservation | None:
        payload = frame.payload
        if len(payload) < WHOOP4_MIN_FRAME:
            return None
        if payload[WHOOP4_PACKET_TYPE_OFFSET] != WHOOP4_REALTIME_TYPE:
            return None  # not a realtime packet — a future decoder's business
        ts_seconds = int.from_bytes(
            payload[WHOOP4_TS_OFFSET : WHOOP4_TS_OFFSET + 4], "little"
        )
        subseconds = int.from_bytes(
            payload[WHOOP4_SUBSEC_OFFSET : WHOOP4_SUBSEC_OFFSET + 2], "little"
        )
        bpm = float(payload[WHOOP4_HR_OFFSET])
        ts = datetime.fromtimestamp(ts_seconds, tz=UTC) + timedelta(
            microseconds=subseconds * 1_000_000 // SUBSECOND_DENOMINATOR
        )
        return DecodedObservation(
            ts=ts, bpm=bpm, source_frame_epoch_ms=frame.epoch_ms
        )
