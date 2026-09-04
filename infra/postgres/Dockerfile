# Somatriq PostgreSQL — pinned image with TimescaleDB + pgvector (spec §53, ADR 0001/0002)
#
# timescaledb-ha ships PostgreSQL + TimescaleDB + pgvector in one maintained image.
# PIN THE EXACT DIGEST before the first production deploy and record it in
# infra/postgres/versions.env (spec §53: never rely on `latest`).
ARG TS_IMAGE=timescale/timescaledb-ha:pg16-ts2.17
FROM ${TS_IMAGE}

COPY init/*.sql /docker-entrypoint-initdb.d/
