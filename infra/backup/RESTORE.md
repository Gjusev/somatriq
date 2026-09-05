# Restore runbook — database + raw blob store (ADR 0016)

The backup service writes nightly `pg_dump --format=custom` archives plus
sha256 sidecars to `/backups/db/`, weekly raw-store archives to
`/backups/raw/`, and an append-only `manifest.jsonl` at the backup root.
This runbook restores both layers onto the running compose project. Every
step runs on the VPS from the project root (where `compose.yml` lives).

All restore commands go through the **somatriq_backup** container: it mounts
`/backups`, sits on the private `somatriq` network next to
`somatriq_postgres`, and (since the ADR 0016 increment) carries
`pg_restore`/`sha256sum` in the shared python image.

## 0. Pick the artifact and verify it

```sh
docker compose exec somatriq_backup sh -c 'tail -20 /backups/manifest.jsonl'
# newest status:"ok" db line → note its file, e.g. db/somatriq-20260905-030000.dump

docker compose exec -e DUMP=/backups/db/somatriq-20260905-030000.dump \
  somatriq_backup sh -c 'cd "$(dirname "$DUMP")" && sha256sum -c "$(basename "$DUMP").sha256"'
# must print: <name>: OK
```

If the checksum fails, take the next-older artifact from the manifest —
never restore an unverified dump.

## 1. Stop the writers (keep postgres + backup running)

```sh
docker compose stop somatriq_api somatriq_worker somatriq_scheduler \
  somatriq_agent somatriq_mcp somatriq_telegram somatriq_notifications \
  somatriq_web somatriq_migrate 2>/dev/null || true
```

`somatriq_migrate` is a run-once job; the `|| true` covers it not running.

## 2. Restore the database

```sh
docker compose exec -e DUMP=/backups/db/somatriq-20260905-030000.dump \
  somatriq_backup sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore \
     -h somatriq_postgres -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     --clean --if-exists --no-owner "$DUMP"'
```

- `--clean --if-exists` drops existing objects first, so this works over a
  partially-populated or empty database alike.
- The dumps are TimescaleDB-aware: `--format=custom` carries the pre/post
  restore hooks inside the archive, so plain `pg_restore` replays them. If a
  manual psql session is ever needed around a restore, the hooks are
  `SELECT timescaledb_pre_restore();` before and
  `SELECT timescaledb_post_restore();` after.
- Restoring into a **fresh volume**: `pg_restore` may complain about roles
  or extensions already absent/present; `--if-exists` covers the common
  cases, and errors about `plpgsql` can be ignored.

## 3. Restore the raw blob store (weekly archive)

Only needed when raw frames are also lost (the nightly db dump does not
contain them). Pick the newest `raw/...tar.gz` with `status:"ok"` in the
manifest and verify it first (same `sha256sum -c` pattern as step 0).

```sh
# DESTRUCTIVE: wipes the current raw volume contents before replacing them.
# Skip the rm if you deliberately want to merge with what is there.
docker compose run --rm --no-deps --entrypoint sh somatriq_backup \
  -c 'rm -rf /raw/* /raw/.[!.]* 2>/dev/null; \
      tar -xzf /backups/raw/somatriq-raw-YYYYMMDD-HHMMSS.tar.gz -C /raw --strip-components=1'
```

(The archive was created with `tar.add(/raw, arcname="raw")`, hence the
`--strip-components=1`.)

## 4. Start everything again

```sh
docker compose up -d
```

## 5. Verify

```sh
# readiness — must answer 200 with the database reachable
curl -fsS https://<SOMATRIQ_HOST>/api/ready

# metric read — sign in with the account passphrase, then read a metric
TOKEN=$(curl -fsS -H 'Content-Type: application/json' \
  -d '{"username":"<user>","password":"<passphrase>"}' \
  https://<SOMATRIQ_HOST>/api/v1/auth/login | python -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
curl -fsS -H "Authorization: Bearer $TOKEN" \
  'https://<SOMATRIQ_HOST>/api/v1/metrics/daily?days=7'
```

Expect the days you had before the incident (oldest-first rows, one per
local day). A `401 INVALID_CREDENTIALS` on login after a successful restore
means the dump predates the last passphrase change — that is expected
behavior of a logical restore, not a failure. In the web app, the Today /
Daily cards rendering real numbers is the human check.

## Notes

- **Fresh-VPS drill (§172)**: `git clone` the project, fill `.env`, `docker
  compose up -d` (empty database), then follow steps 0-5 against the backup
  volume copied over (`tar` the whole `/backups` tree — manifest included).
- Retention on the backup volume is 14 daily dumps / 4 weekly raws; anything
  older is already gone. Copy artifacts off-server (see ADR 0016 S3 push,
  still a stub) before they age out if you need longer history.
- Dokploy's own volume/database backups remain the secondary layer for
  fast volume-level recovery (ADR 0016); this runbook is the authoritative
  path.
