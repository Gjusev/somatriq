-- Somatriq first-boot extensions. Schemas and tables are created by Alembic
-- (ADR 0002: hand-written migrations; nothing mutates schema at API startup).
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
