"""SQLAlchemy models for the M1+M2 slices (schemas per spec §54-61, §122-123).

Identity keeps seed rows for the single local user, one synthetic device and
one data source (M1); M2 adds the local account credential, pairing sessions
and hashed device tokens (ADR 0015), plus the raw blob registry (ADR 0003).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

metadata = MetaData()  # schema-qualified per table via __table_args__


class Base(DeclarativeBase):
    metadata = metadata


# ── identity (§55) ───────────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "identity"}

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=func.gen_random_uuid()
    )
    display_name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = {"schema": "identity"}

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.users.id"))
    name: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    active_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    active_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataSource(Base):
    __tablename__ = "data_sources"
    __table_args__ = {"schema": "identity"}

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=func.gen_random_uuid()
    )
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.devices.id"))
    provider: Mapped[str] = mapped_column(Text)
    collector: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── identity: local account + device credentials (§122-123, ADR 0015) ───


class AccountCredential(Base):
    """Single local account; the password never leaves this table hashed."""

    __tablename__ = "account_credentials"
    __table_args__ = {"schema": "identity"}

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("identity.users.id"), primary_key=True
    )
    username: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PairingSession(Base):
    """Short-lived pairing code; only its sha256 is authoritative (ADR 0015)."""

    __tablename__ = "pairing_sessions"
    __table_args__ = {"schema": "identity"}

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.users.id"))
    code_hash: Mapped[str] = mapped_column(Text, unique=True)
    code_hint: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending")  # pending | consumed | expired
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("identity.devices.id"))


class DeviceToken(Base):
    """Device credential; the token itself exists only at mint/confirm time."""

    __tablename__ = "device_tokens"
    __table_args__ = {"schema": "identity"}

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=func.gen_random_uuid()
    )
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.devices.id"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ── raw (§48-49, ADR 0003) ───────────────────────────────────────────────


class RawBatch(Base):
    """Registry row for one verbatim compressed blob on a volume."""

    __tablename__ = "raw_batches"
    __table_args__ = {"schema": "raw"}

    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ingest.batches.batch_id"), primary_key=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.devices.id"))
    codec: Mapped[str] = mapped_column(Text)
    journal_version: Mapped[int] = mapped_column(Integer)
    frame_count: Mapped[int] = mapped_column(Integer)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    blob_path: Mapped[str] = mapped_column(Text)
    storage_state: Mapped[str] = mapped_column(Text, default="confirmed")  # confirmed
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ── ingest (§56) ─────────────────────────────────────────────────────────


class IngestBatch(Base):
    __tablename__ = "batches"
    __table_args__ = {"schema": "ingest"}

    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.users.id"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.devices.id"))
    schema_version: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    records_received: Mapped[int] = mapped_column(Integer)
    records_inserted: Mapped[int] = mapped_column(Integer)
    records_duplicate: Mapped[int] = mapped_column(Integer)
    # Raw coverage persisted with the batch so a replay acks the raw truth
    # that was stored the first time (ADR 0003 + ADR 0006 replay semantics).
    raw_frame_count: Mapped[int] = mapped_column(Integer, default=0)
    raw_bytes_stored: Mapped[int] = mapped_column(BigInteger, default=0)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = {"schema": "ingest"}

    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ingest.batches.batch_id"), primary_key=True
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IngestFailure(Base):
    __tablename__ = "failures"
    __table_args__ = {"schema": "ingest"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    error_code: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── timeseries (§57) ─────────────────────────────────────────────────────


class HeartRate(Base):
    __tablename__ = "heart_rate"
    __table_args__ = {"schema": "timeseries"}

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    source_record_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    bpm: Mapped[float] = mapped_column(Float)
    decoder_version: Mapped[str | None] = mapped_column(Text)
    raw_batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ── system (§64: algorithm registry lands later; §153 metric catalog) ────


class Metric(Base):
    __tablename__ = "metrics"
    __table_args__ = {"schema": "system"}

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    unit: Mapped[str] = mapped_column(Text)
    valid_min: Mapped[float | None] = mapped_column(Float)
    valid_max: Mapped[float | None] = mapped_column(Float)
    expected_cadence_seconds: Mapped[int | None] = mapped_column(Integer)
    valid_aggregations: Mapped[list[str]] = mapped_column(ARRAY(Text))


class ReplayedObservation(Base):
    """Parallel decoder-version output of replay reprocessing (ADR 0012).

    The live heart_rate hypertable is the ingest-time interpretation and is
    never rewritten; replay writes here keyed by decoder_version.
    """

    __tablename__ = "replayed_observations"
    __table_args__ = {"schema": "timeseries"}

    decoder_version: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    source_record_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    bpm: Mapped[float] = mapped_column(Float)
    raw_batch_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    source_frame_epoch_ms: Mapped[int] = mapped_column(BigInteger)
    replayed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
