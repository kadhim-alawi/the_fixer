"""NovaCart database schema.

NovaCart is a simulated e-commerce company. The agent investigates it through
tools; it never sees this module. Rows here are real and are really queried --
nothing about the agent's findings is precomputed.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Session(Base):
    """One visit to the storefront."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Only `ts` is indexed. Every query filters a time range first and then
    # groups, so standalone platform/region indexes are never chosen by the
    # planner -- and at 450k rows they cost 15MB for nothing.
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    platform: Mapped[str] = mapped_column(String(16))  # web|ios|android
    region: Mapped[str] = mapped_column(String(8))
    traffic_source: Mapped[str] = mapped_column(String(16))
    app_version: Mapped[str] = mapped_column(String(16))

    # Funnel stages. Each is monotonic: converted implies checkout_started.
    viewed_product: Mapped[bool] = mapped_column(Boolean, default=True)
    added_to_cart: Mapped[bool] = mapped_column(Boolean, default=False)
    checkout_started: Mapped[bool] = mapped_column(Boolean, default=False)
    converted: Mapped[bool] = mapped_column(Boolean, default=False)

    # Set when checkout_started but not converted.
    abandon_stage: Mapped[str | None] = mapped_column(String(24), nullable=True)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    region: Mapped[str] = mapped_column(String(8))
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), index=True)  # paid|failed|pending


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    order_id: Mapped[int] = mapped_column(Integer, index=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    provider: Mapped[str] = mapped_column(String(24))
    profile: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), index=True)  # success|failed
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    latency_ms: Mapped[int] = mapped_column(Integer)


class Deployment(Base):
    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    ref: Mapped[str] = mapped_column(String(16), unique=True)  # e.g. "8472"
    service: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[str] = mapped_column(String(16))
    author: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="live")  # live|rolled_back


class ConfigEntry(Base):
    """Runtime configuration. Deliberately persists independently of deployments --
    this is what makes 'rollback the deploy' an honest-but-wrong remediation."""

    __tablename__ = "config_entries"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(64))
    previous_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_ts: Mapped[datetime] = mapped_column(DateTime)
    updated_by: Mapped[str] = mapped_column(String(48))
    description: Mapped[str] = mapped_column(Text, default="")


class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    rollout_pct: Mapped[int] = mapped_column(Integer, default=100)
    updated_ts: Mapped[datetime] = mapped_column(DateTime)
    updated_by: Mapped[str] = mapped_column(String(48))
    description: Mapped[str] = mapped_column(Text, default="")


class ServiceHealth(Base):
    """Point-in-time infrastructure samples, one row per service per sim-minute."""

    __tablename__ = "service_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    service: Mapped[str] = mapped_column(String(32), index=True)
    error_rate: Mapped[float] = mapped_column(Float)
    latency_p95_ms: Mapped[int] = mapped_column(Integer)
    cpu_pct: Mapped[float] = mapped_column(Float)
    healthy: Mapped[bool] = mapped_column(Boolean, default=True)


class LogEntry(Base):
    __tablename__ = "log_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    service: Mapped[str] = mapped_column(String(32), index=True)
    level: Mapped[str] = mapped_column(String(8), index=True)  # INFO|WARN|ERROR
    platform: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    message: Mapped[str] = mapped_column(Text)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    platform: Mapped[str | None] = mapped_column(String(16), nullable=True)
    subject: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    tags: Mapped[str] = mapped_column(String(120), default="")


class WorldState(Base):
    """Scenario bookkeeping. Never exposed through any agent tool."""

    __tablename__ = "world_state"

    key: Mapped[str] = mapped_column(String(48), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
