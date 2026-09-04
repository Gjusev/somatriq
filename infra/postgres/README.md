# Somatriq PostgreSQL image

`timescale/timescaledb-ha` (PostgreSQL 16 + TimescaleDB + pgvector) with a
first-boot extension init script.

## Version pinning (spec §53)

Before the first production deploy, pin the exact digest:

```bash
docker manifest inspect timescale/timescaledb-ha:pg16-ts2.17   # note the digest
```

Then record in `versions.env`:

```text
# example format — fill with real values at pin time
TIMESCALE_IMAGE_DIGEST=sha256:...
POSTGRESQL_VERSION=16.x
TIMESCALEDB_VERSION=2.17.x
PGVECTOR_VERSION=0.8.x
```

Extension upgrades (major TimescaleDB bumps) happen only through a tested
migration with `ALTER EXTENSION ... UPDATE` plus backup verification (ADR 0016).
