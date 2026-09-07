"""Backup service deterministic logic (ADR 0016, local-first increment).

The service is a script at infra/backup/backup_loop.py — compose mounts it
into the somatriq_backup container; it is not a uv-workspace package — so it
is imported by path. These tests cover the pure parts (URL parsing, manifest
line handling, retention selection, weekly cadence, catch-up scheduling) and
the run orchestration with pg_dump faked (run_pg_dump is the only subprocess
boundary). No database, no network.
"""

import hashlib
import logging
import sys
import tarfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# The script lives outside the workspace packages; put its directory on
# sys.path so `import backup_loop` resolves (unique module name in the repo).
INFRA_BACKUP = Path(__file__).resolve().parents[2] / "infra" / "backup"
sys.path.insert(0, str(INFRA_BACKUP))

import backup_loop as bl  # noqa: E402

T0 = datetime(2026, 9, 5, 3, 0, 0, tzinfo=UTC)

PG_ASYNC_URL = "postgresql+asyncpg://somatriq:pw%40word@somatriq_postgres:5432/somatriq"


def reset_health() -> None:
    bl.set_health(mode="active", last_backup_at=None, last_status=None, last_error=None)


def make_line(n: int, kind: str = "db", status: str = "ok", base: datetime = T0) -> bl.ManifestLine:
    stamp = (base + timedelta(hours=n)).strftime("%Y%m%d-%H%M%S")
    ts = (base + timedelta(hours=n)).isoformat()
    name = f"somatriq-{stamp}.dump" if kind == "db" else f"somatriq-raw-{stamp}.tar.gz"
    return bl.ManifestLine(
        ts=ts, kind=kind, file=f"{kind}/{name}", bytes=100 + n, sha256=f"{n:064x}", status=status
    )


def write_artifact(backup_dir: Path, line: bl.ManifestLine) -> None:
    path = backup_dir / line.file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * line.bytes)


def make_config(tmp_path: Path) -> bl.BackupConfig:
    return bl.BackupConfig(
        database_url=PG_ASYNC_URL,
        backup_dir=tmp_path / "backups",
        raw_dir=tmp_path / "raw",
        interval=timedelta(hours=24),
        initial_delay=timedelta(0),
    )


# ── URL parsing ───────────────────────────────────────────────────────────


def test_to_pg_url_strips_asyncpg_driver_and_keeps_encoding() -> None:
    # Only the scheme changes: the percent-encoded password survives as-is.
    assert (
        bl.to_pg_url(PG_ASYNC_URL)
        == "postgres://somatriq:pw%40word@somatriq_postgres:5432/somatriq"
    )


def test_to_pg_url_accepts_plain_postgres_schemes() -> None:
    assert (
        bl.to_pg_url("postgres://u:p@somatriq_postgres:5432/somatriq")
        == "postgres://u:p@somatriq_postgres:5432/somatriq"
    )
    assert (
        bl.to_pg_url("postgresql://u@h:5432/db?sslmode=disable")
        == "postgres://u@h:5432/db?sslmode=disable"
    )


def test_to_pg_url_rejects_foreign_schemes_and_hostless_dsns() -> None:
    with pytest.raises(bl.BackupError, match="postgres DSN"):
        bl.to_pg_url("mysql://u:p@somatriq_postgres:3306/somatriq")
    with pytest.raises(bl.BackupError, match="host"):
        bl.to_pg_url("postgresql:///somatriq")


# ── manifest line handling ────────────────────────────────────────────────


def test_manifest_line_roundtrip() -> None:
    line = bl.ManifestLine(
        ts=T0.isoformat(),
        kind="db",
        file="db/somatriq-20260905-030000.dump",
        bytes=42,
        sha256="ab" * 32,
        status="ok",
    )
    assert bl.parse_manifest_line(line.to_json()) == line


def test_parse_manifest_line_rejects_malformed_and_blank() -> None:
    assert bl.parse_manifest_line("") is None
    assert bl.parse_manifest_line("   ") is None
    assert bl.parse_manifest_line("not json") is None
    assert bl.parse_manifest_line("[1, 2]") is None
    assert bl.parse_manifest_line('{"ts": "x"}') is None  # missing fields
    # Field-level rejections: bad kind/status, wrong types.
    line = bl.ManifestLine(
        ts=T0.isoformat(), kind="db", file="db/x.dump", bytes=1, sha256="a", status="ok"
    )
    assert bl.parse_manifest_line(line.to_json().replace('"db"', '"other"')) is None
    assert bl.parse_manifest_line(line.to_json().replace('"ok"', '"pending"')) is None
    assert bl.parse_manifest_line(line.to_json().replace('"bytes":1', '"bytes":"1"')) is None


def test_parse_manifest_line_rejects_paths_outside_their_kind_dir() -> None:
    # Deletion authority: a line may only reference files under its kind's
    # directory, and never a parent hop — tampered lines are dropped, not
    # acted on.
    line = bl.ManifestLine(
        ts=T0.isoformat(),
        kind="db",
        file="db/somatriq-x.dump",
        bytes=1,
        sha256="a",
        status="ok",
    )
    assert bl.parse_manifest_line(line.to_json().replace("db/somatriq", "raw/somatriq")) is None
    assert (
        bl.parse_manifest_line(line.to_json().replace("db/somatriq-x.dump", "db/../../etc/passwd"))
        is None
    )


def test_read_write_manifest_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "manifest.jsonl"
    kept = make_line(1)
    path.write_text("\n{bogus}\n" + kept.to_json() + "\n", encoding="utf-8")
    assert bl.read_manifest(path) == [kept]
    bl.write_manifest(path, [kept])
    assert bl.read_manifest(path) == [kept]


# ── retention selection ───────────────────────────────────────────────────


def test_select_expired_keeps_newest_per_kind() -> None:
    lines = [make_line(n) for n in range(1, 21)]  # 20 db entries, ts ascending
    lines += [make_line(n, kind="raw") for n in range(1, 7)]  # 6 raw entries
    expired = bl.select_expired(lines, keep_daily=14, keep_weekly=4)
    expired_files = {line.file for line in expired}
    # 6 oldest db dumps + 2 oldest raw archives; nothing else.
    assert expired_files == {
        *(
            f"db/somatriq-{(T0 + timedelta(hours=n)).strftime('%Y%m%d-%H%M%S')}.dump"
            for n in range(1, 7)
        ),
        *(
            f"raw/somatriq-raw-{(T0 + timedelta(hours=n)).strftime('%Y%m%d-%H%M%S')}.tar.gz"
            for n in range(1, 3)
        ),
    }


def test_select_expired_under_quota_deletes_nothing() -> None:
    lines = [make_line(n) for n in range(1, 4)]
    assert bl.select_expired(lines) == []


def test_apply_retention_deletes_files_sidecars_and_prunes_lines(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lines = [make_line(n) for n in range(1, 18)]  # 17 db artifacts on disk
    for line in lines:
        write_artifact(tmp_path, line)
        bl.write_sidecar(tmp_path / line.file, line.sha256)
    bl.write_manifest(tmp_path / "manifest.jsonl", lines)

    with caplog.at_level(logging.INFO, logger="somatriq_backup"):
        kept = bl.apply_retention(tmp_path, lines)

    assert len(kept) == 14
    assert bl.read_manifest(tmp_path / "manifest.jsonl") == kept
    for line in lines[:3]:  # the 3 oldest are gone, artifacts AND sidecars
        assert not (tmp_path / line.file).exists()
        assert not (tmp_path / (line.file + bl.SHA256_SUFFIX)).exists()
    for line in lines[3:]:  # the newest 14 stay
        assert (tmp_path / line.file).is_file()
    assert any("deleted" in record.message for record in caplog.records)


def test_apply_retention_never_touches_unaccounted_files(tmp_path: Path) -> None:
    # A file the manifest cannot account for (manual rescue copy, orphan) is
    # never selected for deletion, however old it looks.
    lines = [make_line(n) for n in range(1, 20)]
    for line in lines:
        write_artifact(tmp_path, line)
    orphan = tmp_path / "db" / "manual-rescue-oldest.dump"
    orphan.write_bytes(b"rescue")
    kept = bl.apply_retention(tmp_path, lines)
    assert len(kept) == 14
    assert orphan.is_file()


# ── weekly cadence + catch-up scheduling math ─────────────────────────────


def test_is_weekly_every_seventh_successful_dump() -> None:
    assert [bl.is_weekly(n) for n in (0, 1, 6, 7, 8, 13, 14, 15)] == [
        False,
        False,
        False,
        True,
        False,
        False,
        True,
        False,
    ]


def test_seconds_until_next_catchup_semantics() -> None:
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    interval = timedelta(hours=24)
    # Fresh start (no attempt yet): due immediately — the caller adds the
    # settle delay, so a service that was down through its slot backs up now.
    assert bl.seconds_until_next(None, now, interval) == 0.0
    # Two hours into the cycle: 22 hours remain.
    assert bl.seconds_until_next(now - timedelta(hours=2), now, interval) == 22 * 3600.0
    # 30h since the last attempt (the slot was missed): run now, not next week.
    assert bl.seconds_until_next(now - timedelta(hours=30), now, interval) == 0.0


# ── run orchestration (pg_dump faked) ─────────────────────────────────────


def test_run_backup_once_dump_sidecar_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    config.raw_dir.mkdir(parents=True)

    def fake_pg_dump(pg_url: str, out_file: Path) -> None:
        assert pg_url == "postgres://somatriq:pw%40word@somatriq_postgres:5432/somatriq"
        out_file.write_bytes(b"PGDMP-custom-format-payload")

    monkeypatch.setattr(bl, "run_pg_dump", fake_pg_dump)

    assert bl.run_backup_once(config, now=T0) is True

    dump = config.backup_dir / "db" / "somatriq-20260905-030000.dump"
    digest = hashlib.sha256(b"PGDMP-custom-format-payload").hexdigest()
    assert dump.is_file()
    assert dump.with_name(dump.name + bl.SHA256_SUFFIX).read_text(encoding="utf-8") == (
        f"{digest}  {dump.name}\n"
    )
    lines = bl.read_manifest(config.backup_dir / "manifest.jsonl")
    assert len(lines) == 1
    assert lines[0].kind == "db"
    assert lines[0].sha256 == digest
    assert lines[0].bytes == len(b"PGDMP-custom-format-payload")
    assert lines[0].status == "ok"


def test_run_backup_once_weekly_raw_archive_on_seventh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    config.raw_dir.mkdir(parents=True)
    (config.raw_dir / "frame.bin").write_bytes(b"raw-blob")
    # Six prior successful dumps: this run is the 7th → weekly raw archive.
    prior = [make_line(n, base=T0 - timedelta(hours=7)) for n in range(1, 7)]
    (config.backup_dir / "db").mkdir(parents=True, exist_ok=True)
    bl.write_manifest(config.backup_dir / "manifest.jsonl", prior)

    monkeypatch.setattr(bl, "run_pg_dump", lambda pg_url, out_file: out_file.write_bytes(b"dump"))
    bl.run_backup_once(config, now=T0)

    raw = config.backup_dir / "raw" / "somatriq-raw-20260905-030000.tar.gz"
    assert raw.is_file()
    with tarfile.open(raw, "r:gz") as tar:
        assert tar.getnames() == ["raw", "raw/frame.bin"]
    lines = bl.read_manifest(config.backup_dir / "manifest.jsonl")
    assert [line.kind for line in lines] == ["db"] * 6 + ["db", "raw"]

    # Not the 7th → no raw archive at all.
    config2 = make_config(tmp_path / "second")
    config2.raw_dir.mkdir(parents=True)
    monkeypatch.setattr(bl, "run_pg_dump", lambda pg_url, out_file: out_file.write_bytes(b"dump"))
    bl.run_backup_once(config2, now=T0)
    assert not (config2.backup_dir / "raw").exists()


def test_run_backup_once_records_failure_and_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    config.raw_dir.mkdir(parents=True)

    def failing_pg_dump(pg_url: str, out_file: Path) -> None:
        out_file.write_bytes(b"partial")  # torn write — must be cleaned up
        raise bl.BackupError("pg_dump failed: server did not respond")

    monkeypatch.setattr(bl, "run_pg_dump", failing_pg_dump)
    with pytest.raises(bl.BackupError, match="pg_dump failed"):
        bl.run_backup_once(config, now=T0)

    assert not (config.backup_dir / "db" / "somatriq-20260905-030000.dump").exists()
    lines = bl.read_manifest(config.backup_dir / "manifest.jsonl")
    assert [line.status for line in lines] == ["error"]
    assert lines[0].sha256 == ""


def test_run_backup_once_applies_retention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    config.raw_dir.mkdir(parents=True)
    # 14 prior dumps ending an hour before this run (so the new one is newest).
    prior = [make_line(n, base=T0 - timedelta(hours=15)) for n in range(1, 15)]
    for line in prior:
        write_artifact(config.backup_dir, line)
    bl.write_manifest(config.backup_dir / "manifest.jsonl", prior)

    monkeypatch.setattr(bl, "run_pg_dump", lambda pg_url, out_file: out_file.write_bytes(b"dump"))
    bl.run_backup_once(config, now=T0)

    # 15 ok dumps → the oldest fell out of the 14-day window.
    lines = bl.read_manifest(config.backup_dir / "manifest.jsonl")
    ok = [line for line in lines if line.status == "ok"]
    assert len(ok) == 14
    assert not (config.backup_dir / prior[0].file).exists()


# ── loop: failures never crash it, cadence follows attempts ───────────────


def test_backup_loop_survives_failure_and_waits_full_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_health()
    config = make_config(tmp_path)
    attempts: list[datetime] = []

    def failing_run(cfg: bl.BackupConfig, *, now: datetime | None = None) -> bool:
        attempts.append(now or datetime.now(UTC))
        raise bl.BackupError("database unreachable")

    monkeypatch.setattr(bl, "run_backup_once", failing_run)

    stop = threading.Event()
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        stop.set()  # end the loop after the first post-attempt sleep

    clock = {"t": T0}

    def fake_now() -> datetime:
        return clock["t"]

    bl.backup_loop(config, stop=stop, sleep=fake_sleep, now=fake_now)

    # First attempt ran immediately (initial_delay 0, catch-up); the failure
    # was recorded, not raised, and the next attempt waits a full interval —
    # no tight retry loop against a down database.
    assert len(attempts) == 1
    assert sleeps == [24 * 3600.0]
    snapshot = bl.health_snapshot()
    assert snapshot["last_status"] == "error"
    assert snapshot["last_error"] == "database unreachable"
    assert snapshot["last_backup_at"] is None
    reset_health()


def test_backup_loop_advances_last_backup_at_only_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_health()
    config = make_config(tmp_path)
    calls: list[str] = []

    def flaky_run(cfg: bl.BackupConfig, *, now: datetime | None = None) -> bool:
        calls.append("run")
        if len(calls) == 1:
            raise bl.BackupError("first night fails")
        return True

    monkeypatch.setattr(bl, "run_backup_once", flaky_run)

    stop = threading.Event()

    def fake_sleep(seconds: float) -> None:
        if len(calls) >= 2:
            stop.set()

    bl.backup_loop(
        config,
        stop=stop,
        sleep=fake_sleep,
        now=lambda: T0,
    )

    assert len(calls) == 2
    snapshot = bl.health_snapshot()
    assert snapshot["last_status"] == "ok"
    assert snapshot["last_backup_at"] == T0.isoformat()
    assert snapshot["last_error"] is None
    reset_health()
