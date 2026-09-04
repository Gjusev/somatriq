"""Journal v1 codec tests: round-trip, tamper detection, structure errors."""

import hashlib
from pathlib import Path

import pytest
import zstandard
from somatriq_replay.journal import JournalError, iter_frames, verify_blob


def build_journal(frames: list[tuple[int, bytes]]) -> bytes:
    raw = b"".join(
        len(payload).to_bytes(4, "big") + epoch_ms.to_bytes(8, "big") + payload
        for epoch_ms, payload in frames
    )
    return zstandard.ZstdCompressor().compress(raw)


def test_round_trip() -> None:
    frames = [(1759970000000 + i, bytes([0x0F, i])) for i in range(5)]
    blob = build_journal(frames)
    decoded = iter_frames(blob)
    assert len(decoded) == 5
    assert decoded[0].epoch_ms == 1759970000000
    assert decoded[0].payload == b"\x0f\x00"
    assert decoded[-1].payload == b"\x0f\x04"


def test_empty_journal_rejected() -> None:
    blob = zstandard.ZstdCompressor().compress(b"")
    with pytest.raises(JournalError, match="no frames"):
        iter_frames(blob)


def test_truncated_frame_rejected() -> None:
    raw = (2).to_bytes(4, "big") + (1).to_bytes(8, "big") + b"\x01"  # needs 2 bytes
    with pytest.raises(JournalError, match="truncated"):
        iter_frames(zstandard.ZstdCompressor().compress(raw))


def test_implausible_length_rejected() -> None:
    raw = (10_000_000).to_bytes(4, "big") + (1).to_bytes(8, "big")
    with pytest.raises(JournalError, match="implausible"):
        iter_frames(zstandard.ZstdCompressor().compress(raw))


def test_verify_blob_sha_mismatch(tmp_path: Path) -> None:
    blob = build_journal([(1, b"\x01\x02")])
    p = tmp_path / "b.zst"
    p.write_bytes(blob)
    with pytest.raises(JournalError, match="sha mismatch"):
        verify_blob(p, expected_sha256="0" * 64, expected_frame_count=1)


def test_verify_blob_count_mismatch(tmp_path: Path) -> None:
    blob = build_journal([(1, b"\x01\x02"), (2, b"\x03\x04")])
    p = tmp_path / "b.zst"
    p.write_bytes(blob)
    sha = hashlib.sha256(blob).hexdigest()
    with pytest.raises(JournalError, match="frame count"):
        verify_blob(p, expected_sha256=sha, expected_frame_count=3)


def test_verify_blob_ok(tmp_path: Path) -> None:
    blob = build_journal([(100, b"\x01\x02"), (200, b"\x03\x04")])
    p = tmp_path / "b.zst"
    p.write_bytes(blob)
    report = verify_blob(
        p, expected_sha256=hashlib.sha256(blob).hexdigest(), expected_frame_count=2
    )
    assert report.frame_count == 2
    assert report.byte_size == len(blob)
    assert report.first_epoch_ms == 100
    assert report.last_epoch_ms == 200
