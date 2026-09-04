"""Somatriq Raw Journal v1 decoder (format frozen in contracts/raw.py).

    entry   := u32_be length N | u64_be epoch_ms | N bytes frame
    journal := zstd( entry* )

The server treats stored blobs as opaque at ingest (ADR 0003); THIS module
is the M4 tooling that makes them readable again — byte-fidelity against
the raw_batches row is the whole point of "verify replay" (§197).
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import zstandard

# Safety bounds: a single frame larger than this is journal corruption.
FRAME_MAX_BYTES = 64 * 1024


class JournalError(ValueError):
    """The blob is not a valid journal segment — corruption, never guess."""


@dataclass(frozen=True)
class Frame:
    epoch_ms: int
    payload: bytes


@dataclass(frozen=True)
class JournalReport:
    frame_count: int
    sha256: str
    byte_size: int
    first_epoch_ms: int | None
    last_epoch_ms: int | None


def decompress(blob: bytes) -> bytes:
    try:
        return zstandard.ZstdDecompressor().decompress(
            blob, max_output_size=256 * 1024 * 1024
        )
    except zstandard.ZstdError as exc:
        msg = f"blob is not valid zstd: {exc}"
        raise JournalError(msg) from exc


def iter_frames(blob: bytes) -> list[Frame]:
    """Decode every entry; any structural deviation is JournalError."""
    data = decompress(blob)
    frames: list[Frame] = []
    i = 0
    n = len(data)
    while i < n:
        if i + 12 > n:
            msg = f"truncated entry header at byte {i} ({n - i} bytes left)"
            raise JournalError(msg)
        length = int.from_bytes(data[i : i + 4], "big")
        epoch_ms = int.from_bytes(data[i + 4 : i + 12], "big")
        i += 12
        if length == 0 or length > FRAME_MAX_BYTES:
            msg = f"implausible frame length {length} at byte {i - 12}"
            raise JournalError(msg)
        if i + length > n:
            msg = f"truncated frame at byte {i}: need {length}, have {n - i}"
            raise JournalError(msg)
        frames.append(Frame(epoch_ms=epoch_ms, payload=data[i : i + length]))
        i += length
    if not frames:
        msg = "journal contains no frames"
        raise JournalError(msg)
    return frames


def read_blob(blob_path: Path | str) -> bytes:
    return Path(blob_path).read_bytes()


def verify_blob(
    blob_path: Path | str,
    *,
    expected_sha256: str,
    expected_frame_count: int,
) -> JournalReport:
    """Full §197 replay verification of one stored blob.

    Raises JournalError on any mismatch — sha, structure, or count.
    """
    blob = read_blob(blob_path)
    sha = hashlib.sha256(blob).hexdigest()
    if sha != expected_sha256:
        msg = f"sha mismatch: stored row says {expected_sha256}, blob is {sha}"
        raise JournalError(msg)
    frames = iter_frames(blob)
    if len(frames) != expected_frame_count:
        msg = f"frame count mismatch: row says {expected_frame_count}, journal has {len(frames)}"
        raise JournalError(msg)
    return JournalReport(
        frame_count=len(frames),
        sha256=sha,
        byte_size=len(blob),
        first_epoch_ms=frames[0].epoch_ms,
        last_epoch_ms=frames[-1].epoch_ms,
    )
