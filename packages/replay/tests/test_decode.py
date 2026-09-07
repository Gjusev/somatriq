"""WHOOP 4.0 realtime HR decoder tests against synthetic frames built from
the documented layout (envelope + REALTIME_DATA payload, facts only)."""

from somatriq_replay.decode import WHOOP4_REALTIME_HR_V1, Whoop4RealtimeHrDecoder
from somatriq_replay.journal import Frame


def whoop4_realtime_frame(*, ts_seconds: int, subseconds: int, bpm: int, seq: int = 0) -> bytes:
    frame = bytearray(14)
    frame[0] = 0x01  # SOF (fact: u8@0)
    frame[1:3] = (14 - 6).to_bytes(2, "big")  # length u16 BE @1 (payload length)
    frame[3] = 0x00  # crc8 placeholder — replay does not re-verify CRC (M4 scope)
    frame[4] = 40  # packet_type REALTIME_DATA
    frame[5] = seq
    frame[6:10] = ts_seconds.to_bytes(4, "little")
    frame[10:12] = subseconds.to_bytes(2, "little")
    frame[12] = bpm
    frame[13] = 0  # rr_count
    return bytes(frame)


def test_decodes_heart_rate() -> None:
    decoder = Whoop4RealtimeHrDecoder()
    frame = Frame(
        epoch_ms=1759970000000,
        payload=whoop4_realtime_frame(ts_seconds=1759970000, subseconds=16384, bpm=62),
    )
    obs = decoder.decode(frame)
    assert obs is not None
    assert obs.bpm == 62.0
    # subseconds 16384/32768 = exactly half a second past the u32 epoch
    assert obs.ts.timestamp() == 1759970000.5
    assert obs.source_frame_epoch_ms == 1759970000000
    assert obs.source_record_id == "replay-frame-1759970000000"


def test_ignores_non_realtime_packet() -> None:
    decoder = Whoop4RealtimeHrDecoder()
    other = bytearray(whoop4_realtime_frame(ts_seconds=1, subseconds=0, bpm=70))
    other[4] = 3  # some other packet type
    assert decoder.decode(Frame(epoch_ms=1, payload=bytes(other))) is None


def test_ignores_short_frame() -> None:
    decoder = Whoop4RealtimeHrDecoder()
    short = bytes([0x01, 0x00, 0x08, 0x00, 40, 0x00, 0x01])
    assert decoder.decode(Frame(epoch_ms=1, payload=short)) is None


def test_decoder_version_is_pinned() -> None:
    assert Whoop4RealtimeHrDecoder().decoder_version == WHOOP4_REALTIME_HR_V1
    assert WHOOP4_REALTIME_HR_V1 == "whoop4-realtime-hr/v1"


def test_subsecond_resolution() -> None:
    decoder = Whoop4RealtimeHrDecoder()
    base = 1759970000
    full = decoder.decode(
        Frame(epoch_ms=0, payload=whoop4_realtime_frame(ts_seconds=base, subseconds=0, bpm=60))
    )
    half = decoder.decode(
        Frame(epoch_ms=0, payload=whoop4_realtime_frame(ts_seconds=base, subseconds=16384, bpm=60))
    )
    assert full is not None and half is not None
    assert (half.ts - full.ts).total_seconds() == 0.5
