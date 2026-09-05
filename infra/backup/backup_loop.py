"""Backup service (ADR 0016 — local-first increment).

Runs as the somatriq_backup container command (compose mounts this directory
read-only at /app/infra/backup). Each night:

  1. pg_dump --format=custom against DATABASE_URL → /backups/db/
     (custom format carries the TimescaleDB pre-data hooks inside the
     archive, so a plain pg_restore replays them — ADR 0016).
  2. sha256 sidecar (sha256sum -c compatible) + one manifest.jsonl line.
  3. Every 7th successful dump also tar+gzip's the raw blob store (/raw →
     /backups/raw/).
  4. Retention: newest 14 daily dumps + newest 4 weekly raw archives stay;
     older artifacts are deleted and their manifest lines pruned. The
     manifest is the sole authority for deletion — files it cannot account
     for are never touched.
  5. Failures set last_status=error in /health and never kill the loop.

Catch-up semantics mirror the M7 brief scheduler: one backup runs shortly
after start (a service that was down through its slot backs up immediately on
restart instead of waiting a full interval).

The ADR's external S3 destination is a deliberate stub this increment
(BACKUP_S3_* envs are empty in production): the local /backups volume is the
destination. Deterministic logic (URL parsing, manifest, retention,
scheduling math) is pure and unit-tested in tests/backup/test_backup_loop.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import tarfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

MANIFEST_NAME = "manifest.jsonl"
SHA256_SUFFIX = ".sha256"
DAILY_KEEP = 14
WEEKLY_KEEP = 4
WEEKLY_EVERY = 7

DEFAULT_INTERVAL_HOURS = 24.0
DEFAULT_INITIAL_DELAY_SECONDS = 30.0

log = logging.getLogger("somatriq_backup")


class BackupError(RuntimeError):
    """A backup step failed; the message is safe for /health (no secrets)."""


# ── health state (thread-shared with the HTTP handler) ───────────────────


_health_lock = threading.Lock()
_health: dict[str, object] = {
    "mode": "active",
    "last_backup_at": None,
    "last_status": None,
    "last_error": None,
}


def set_health(**fields: object) -> None:
    with _health_lock:
        _health.update(fields)


def health_snapshot() -> dict[str, object]:
    with _health_lock:
        return dict(_health)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            body = json.dumps(
                {"status": "ok", "service": "somatriq_backup", **health_snapshot()},
                sort_keys=True,
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args: object) -> None:
        return


# ── pure logic ────────────────────────────────────────────────────────────


def to_pg_url(database_url: str) -> str:
    """postgresql+asyncpg://u:p@h:5432/db → postgres://u:p@h:5432/db.

    Only the scheme is swapped, so any percent-encoding in the userinfo stays
    exactly as configured. Raises BackupError for non-postgres DSNs or a URL
    without a host (pg_dump reads that as a local socket, which the backup
    container does not have).
    """
    parts = urlsplit(database_url)
    if parts.scheme not in ("postgresql+asyncpg", "postgres+asyncpg", "postgresql", "postgres"):
        raise BackupError(f"DATABASE_URL must be a postgres DSN, got scheme {parts.scheme!r}")
    if not parts.netloc:
        raise BackupError("DATABASE_URL must name a host (pg_dump connects over the network)")
    return urlunsplit(("postgres", parts.netloc, parts.path, parts.query, ""))


@dataclass(frozen=True)
class ManifestLine:
    """One backup artifact as recorded in manifest.jsonl."""

    ts: str  # ISO-8601 UTC, written by this service (lexicographically sortable)
    kind: str  # "db" (nightly dump) | "raw" (weekly blob-store archive)
    file: str  # path relative to the backup root, e.g. db/somatriq-...dump
    bytes: int  # 0 for failed artifacts
    sha256: str  # hex digest; "" for failed artifacts
    status: str  # "ok" | "error"

    def to_json(self) -> str:
        return json.dumps(
            {
                "ts": self.ts,
                "kind": self.kind,
                "file": self.file,
                "bytes": self.bytes,
                "sha256": self.sha256,
                "status": self.status,
            },
            separators=(",", ":"),
        )


def parse_manifest_line(line: str) -> ManifestLine | None:
    """One jsonl line → ManifestLine; None for blank or malformed lines.

    The manifest must never crash the service on a torn last write, but a
    line that would direct deletion at an unexpected path is rejected: the
    file must sit under its kind's directory and contain no "..".
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        raw: object = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    ts = raw.get("ts")
    kind = raw.get("kind")
    file = raw.get("file")
    size = raw.get("bytes")
    digest = raw.get("sha256")
    status = raw.get("status")
    if not (
        isinstance(ts, str)
        and isinstance(kind, str)
        and isinstance(file, str)
        and isinstance(size, int)
        and not isinstance(size, bool)
        and isinstance(digest, str)
        and isinstance(status, str)
    ):
        return None
    if kind not in ("db", "raw") or status not in ("ok", "error") or size < 0:
        return None
    if not file.startswith(f"{kind}/") or ".." in file:
        return None
    return ManifestLine(ts=ts, kind=kind, file=file, bytes=size, sha256=digest, status=status)


def read_manifest(path: Path) -> list[ManifestLine]:
    if not path.is_file():
        return []
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        parsed = parse_manifest_line(raw)
        if parsed is not None:
            lines.append(parsed)
    return lines


def write_manifest(path: Path, lines: Sequence[ManifestLine]) -> None:
    """Atomic rewrite (tmp + os.replace) so a crash never truncates the log."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(line.to_json() + "\n" for line in lines), encoding="utf-8")
    os.replace(tmp, path)


def select_expired(
    lines: Sequence[ManifestLine],
    *,
    keep_daily: int = DAILY_KEEP,
    keep_weekly: int = WEEKLY_KEEP,
) -> list[ManifestLine]:
    """Entries past retention: per kind, the newest `keep` survive, the rest
    are returned for deletion. Status does not matter for the count — an
    error line ages out on the same clock so the manifest stays bounded."""
    expired: list[ManifestLine] = []
    for kind, keep in (("db", keep_daily), ("raw", keep_weekly)):
        of_kind = sorted(
            (line for line in lines if line.kind == kind),
            key=lambda line: (line.ts, line.file),
            reverse=True,
        )
        expired.extend(of_kind[keep:])
    return expired


def apply_retention(
    backup_dir: Path,
    lines: Sequence[ManifestLine],
    *,
    keep_daily: int = DAILY_KEEP,
    keep_weekly: int = WEEKLY_KEEP,
) -> list[ManifestLine]:
    """Delete expired artifacts + sidecars, prune their manifest lines.

    Only files the manifest lists are ever deleted — an unaccounted file in
    /backups is left alone (it may be a manual rescue copy). Every deletion
    is logged. Returns the surviving lines (also written back).
    """
    expired = select_expired(lines, keep_daily=keep_daily, keep_weekly=keep_weekly)
    if not expired:
        return list(lines)
    doomed = {line.file for line in expired}
    for rel in sorted(doomed):
        for candidate in (backup_dir / rel, backup_dir / (rel + SHA256_SUFFIX)):
            if candidate.is_file():
                candidate.unlink()
                removed = candidate.relative_to(backup_dir).as_posix()
                log.info("backup retention deleted %s", removed)
    kept = [line for line in lines if line.file not in doomed]
    write_manifest(backup_dir / MANIFEST_NAME, kept)
    return kept


def seconds_until_next(
    last_attempt_at: datetime | None, now: datetime, interval: timedelta
) -> float:
    """Nightly cadence with catch-up: no attempt yet → due immediately (the
    caller adds the short settle delay after start); otherwise wait out the
    remainder of the interval — 0 when the slot was missed while the service
    was down (run now, do not skip: same rule as the M7 morning brief)."""
    if last_attempt_at is None:
        return 0.0
    return max(0.0, (interval - (now - last_attempt_at)).total_seconds())


def is_weekly(ok_dumps: int, every: int = WEEKLY_EVERY) -> bool:
    """Every Nth successful dump also archives the raw blob store."""
    return ok_dumps > 0 and ok_dumps % every == 0


# ── side effects (factored so tests can fake them) ────────────────────────


def sha256_file(path: Path) -> tuple[str, int]:
    """(hex digest, size) streamed in 1 MiB chunks."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def write_sidecar(artifact: Path, digest: str) -> None:
    """sha256sum -c compatible '<digest>  <name>' next to the artifact."""
    artifact.with_name(artifact.name + SHA256_SUFFIX).write_text(
        f"{digest}  {artifact.name}\n", encoding="utf-8"
    )


def run_pg_dump(pg_url: str, out_file: Path) -> None:
    """pg_dump custom format: the TimescaleDB pre-data hooks travel inside
    the archive, so pg_restore --clean replays them (ADR 0016)."""
    completed = subprocess.run(
        ["pg_dump", "--format=custom", f"--file={out_file}", pg_url],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not out_file.is_file():
        stderr_lines = (completed.stderr or "").strip().splitlines()
        detail = stderr_lines[-1] if stderr_lines else f"pg_dump exited {completed.returncode}"
        raise BackupError(f"pg_dump failed: {detail}")


def archive_raw(raw_dir: Path, out_file: Path) -> None:
    if not raw_dir.is_dir():
        raise BackupError(f"raw blob store missing: {raw_dir}")
    with tarfile.open(out_file, "w:gz") as tar:
        tar.add(raw_dir, arcname="raw")
    if not out_file.is_file():
        raise BackupError("raw archive was not created")


def push_to_s3(artifact: Path) -> None:
    """ADR 0016 external-destination push — deliberate stub.

    BACKUP_S3_* envs are empty in production, so the local /backups volume is
    this increment's destination and this hook is a no-op. When S3 lands:
    upload the artifact + its .sha256 sidecar, verify by object HEAD +
    checksum (ADR 0016 "verification is scheduled, not aspirational"), and
    only then apply the remote retention window.
    """
    return None


# ── orchestration ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BackupConfig:
    database_url: str
    backup_dir: Path
    raw_dir: Path
    interval: timedelta = timedelta(hours=24)
    initial_delay: timedelta = timedelta(seconds=DEFAULT_INITIAL_DELAY_SECONDS)
    keep_daily: int = DAILY_KEEP
    keep_weekly: int = WEEKLY_KEEP
    weekly_every: int = WEEKLY_EVERY


def run_backup_once(config: BackupConfig, *, now: datetime | None = None) -> bool:
    """One backup pass: dump → sidecar → manifest → (weekly raw archive) →
    retention. Returns True when the dump succeeded. Failures raise
    BackupError after the attempt is recorded in the manifest — the caller
    (/health state) reports them; the loop itself never dies."""
    started = now or datetime.now(UTC)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    db_dir = config.backup_dir / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    dump_rel = f"db/somatriq-{stamp}.dump"
    dump_path = config.backup_dir / dump_rel
    manifest_path = config.backup_dir / MANIFEST_NAME
    lines = read_manifest(manifest_path)

    pg_url = to_pg_url(config.database_url)
    try:
        run_pg_dump(pg_url, dump_path)
    except BackupError:
        dump_path.unlink(missing_ok=True)  # a torn partial is not a backup
        lines.append(
            ManifestLine(
                ts=started.isoformat(), kind="db", file=dump_rel, bytes=0, sha256="", status="error"
            )
        )
        write_manifest(manifest_path, lines)
        raise

    digest, size = sha256_file(dump_path)
    write_sidecar(dump_path, digest)
    lines.append(
        ManifestLine(
            ts=started.isoformat(), kind="db", file=dump_rel, bytes=size, sha256=digest, status="ok"
        )
    )
    write_manifest(manifest_path, lines)
    push_to_s3(dump_path)
    log.info("db backup written: %s (%d bytes)", dump_rel, size)

    ok_dumps = sum(1 for line in lines if line.kind == "db" and line.status == "ok")
    if is_weekly(ok_dumps, config.weekly_every):
        raw_dir_out = config.backup_dir / "raw"
        raw_dir_out.mkdir(parents=True, exist_ok=True)
        raw_rel = f"raw/somatriq-raw-{stamp}.tar.gz"
        raw_path = config.backup_dir / raw_rel
        archive_raw(config.raw_dir, raw_path)
        raw_digest, raw_size = sha256_file(raw_path)
        write_sidecar(raw_path, raw_digest)
        lines.append(
            ManifestLine(
                ts=started.isoformat(),
                kind="raw",
                file=raw_rel,
                bytes=raw_size,
                sha256=raw_digest,
                status="ok",
            )
        )
        write_manifest(manifest_path, lines)
        push_to_s3(raw_path)
        log.info("raw archive written: %s (%d bytes)", raw_rel, raw_size)

    apply_retention(
        config.backup_dir,
        lines,
        keep_daily=config.keep_daily,
        keep_weekly=config.keep_weekly,
    )
    return True


def backup_loop(
    config: BackupConfig,
    *,
    stop: threading.Event | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Nightly loop: settle delay → catch-up run → interval runs.

    The cadence counts attempts (not successes) so a failing database never
    produces a tight retry loop; a failed night sets last_status=error and
    the next attempt waits a full interval. last_backup_at only advances on
    success.
    """
    last_attempt: datetime | None = None
    if config.initial_delay > timedelta(0):
        log.info(
            "first backup in %ds (catch-up), then every %s",
            int(config.initial_delay.total_seconds()),
            config.interval,
        )
        sleep(config.initial_delay.total_seconds())
    while stop is None or not stop.is_set():
        started = now()
        try:
            run_backup_once(config, now=started)
            set_health(
                last_backup_at=started.isoformat(), last_status="ok", last_error=None
            )
            log.info("backup run completed")
        except Exception as exc:  # noqa: BLE001 — a failed night must never kill the loop
            set_health(last_status="error", last_error=str(exc))
            log.exception("backup run failed")
        last_attempt = now()
        wait = seconds_until_next(last_attempt, now(), config.interval)
        if wait > 0:
            sleep(wait)


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        log.warning("%s is not a number; using %s", name, default)
        return default
    return value if value > 0 else default


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    port = int(os.environ.get("BACKUP_HEALTH_PORT", "8205"))
    config = BackupConfig(
        database_url=os.environ.get("DATABASE_URL", ""),
        backup_dir=Path(os.environ.get("BACKUP_DIR", "/backups")),
        raw_dir=Path(os.environ.get("RAW_DIR", "/raw")),
        interval=timedelta(hours=_env_float("BACKUP_INTERVAL_HOURS", DEFAULT_INTERVAL_HOURS)),
        initial_delay=timedelta(
            seconds=_env_float("BACKUP_INITIAL_DELAY_SECONDS", DEFAULT_INITIAL_DELAY_SECONDS)
        ),
    )
    server = HTTPServer(("0.0.0.0", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info(
        "backup service started (ADR 0016, local-first): dir=%s interval=%s",
        config.backup_dir,
        config.interval,
    )
    try:
        backup_loop(config)
    except KeyboardInterrupt:
        log.info("backup service stopped")


if __name__ == "__main__":
    main()
