"""LocalVolumeRawBlobStore behavior (spec §48-49, ADR 0003).

Pure-filesystem unit tests — no database required. The blob is stored
VERBATIM (it is already zstd; the server never decompresses) at the §49
layout with zero-padded date parts, and the write is atomic.
"""

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from somatriq_api.rawstore import LocalVolumeRawBlobStore

RECEIVED_AT = datetime(2026, 9, 4, 6, 45, 3, tzinfo=UTC)


def test_layout_is_year_month_day_user_device_batch(tmp_path: Path) -> None:
    """Blob lands at §49 layout with zero-padded month/day segments."""
    store = LocalVolumeRawBlobStore(tmp_path)
    user_id, device_id, batch_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    path = store.write(user_id, device_id, batch_id, b"frame-bytes", RECEIVED_AT)

    assert path == (
        tmp_path / "2026" / "09" / "04" / str(user_id) / str(device_id) / f"{batch_id}.zst"
    )
    assert path.read_bytes() == b"frame-bytes"


def test_blob_stored_verbatim(tmp_path: Path) -> None:
    """The payload is opaque compressed bytes — written exactly as received."""
    store = LocalVolumeRawBlobStore(tmp_path)
    payload = bytes(range(256)) * 17  # arbitrary non-utf8-safe bytes

    path = store.write(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), payload, RECEIVED_AT)

    assert path.read_bytes() == payload


def test_replay_overwrites_same_path_atomically(tmp_path: Path) -> None:
    """A re-sent batch rewrites the same path; no temp files are left behind."""
    store = LocalVolumeRawBlobStore(tmp_path)
    ids = (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    store.write(*ids, b"first", RECEIVED_AT)
    path = store.write(*ids, b"second-attempt", RECEIVED_AT)

    assert path.read_bytes() == b"second-attempt"
    leftovers = [p for p in path.parent.iterdir() if p.name != path.name]
    assert leftovers == []


def test_write_is_durable(tmp_path: Path) -> None:
    """The file is fsynced before the final name appears (raw_ack §50)."""
    store = LocalVolumeRawBlobStore(tmp_path)
    path = store.write(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), b"x", RECEIVED_AT)
    # Durability itself is untestable portably; the durable-write contract at
    # least requires the file to exist with full contents on return.
    assert path.is_file()
    assert os.path.getsize(path) == 1


def test_zero_padding_on_single_digit_dates(tmp_path: Path) -> None:
    """January 5 lands in /2026/01/05/, never /2026/1/5/."""
    store = LocalVolumeRawBlobStore(tmp_path)

    path = store.write(
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), b"x", datetime(2027, 1, 5, tzinfo=UTC)
    )

    assert path.relative_to(tmp_path).parts[:3] == ("2027", "01", "05")
