"""知识核持久模型 — schema 领域无关 (domain/severity 取值由领域包定义)。"""
from datetime import datetime, timezone
from sqlalchemy import String, Integer, JSON, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InvariantRegistry(Base):
    __tablename__ = "invariant_registry"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    domain_pack: Mapped[str] = mapped_column(String, index=True)
    domain: Mapped[str] = mapped_column(String)
    spec_ref: Mapped[str] = mapped_column(String, default="")
    executor_kind: Mapped[str] = mapped_column(String)
    location_repo: Mapped[str] = mapped_column(String, index=True)
    location_path: Mapped[str] = mapped_column(String)
    location_test: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String, default="mid")
    status: Mapped[str] = mapped_column(String, default="active")
    origin: Mapped[str] = mapped_column(String, default="hand")
    escape_id: Mapped[str | None] = mapped_column(String, nullable=True)


class GateRun(Base):
    __tablename__ = "gate_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    change_ref: Mapped[str] = mapped_column(String, index=True)
    job_id: Mapped[str] = mapped_column(String, index=True)
    verdict: Mapped[str] = mapped_column(String)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now)
    event: Mapped[str] = mapped_column(String)
    actor: Mapped[str] = mapped_column(String, default="system")
    decision: Mapped[str] = mapped_column(String, default="")
    refs: Mapped[dict] = mapped_column(JSON, default=dict)
