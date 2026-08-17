"""知识核持久模型 — schema 领域无关 (domain/severity 取值由领域包定义)。"""
import warnings
from datetime import datetime, timezone
from sqlalchemy import String, Integer, JSON, DateTime, Boolean, Float, UniqueConstraint, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def ensure_schema(engine) -> None:
    """Create tables and apply the additive migrations used by the trace store."""
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    additions_by_table = {
        "escape_registry": {"fix_ref": "VARCHAR", "missed_by_run_id": "INTEGER",
                            "introduced_at_ts": "DATETIME"},
        # These columns were added after review tracing shipped. Keep this
        # migration additive so existing sweep databases remain readable.
        "review_run": {"status": "VARCHAR", "evidence": "JSON"},
    }
    with engine.begin() as conn:
        for table, additions in additions_by_table.items():
            if table not in inspector.get_table_names():
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, sql_type in additions.items():
                if name in existing:
                    continue
                try:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
                except OperationalError as exc:
                    # A second worker may have applied this additive migration
                    # after the inspector snapshot. Ignore only that race,
                    # never arbitrary migration failures.
                    message = str(exc).lower()
                    if "duplicate column" not in message and "already exists" not in message:
                        raise
        if "review_run" in inspector.get_table_names():
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_review_run_status ON review_run (status)"
            ))
        if "review_finding" in inspector.get_table_names():
            duplicate_count = conn.execute(text(
                "SELECT COUNT(*) FROM ("
                "SELECT run_id, key FROM review_finding "
                "WHERE key IS NOT NULL GROUP BY run_id, key HAVING COUNT(*) > 1"
                ")"
            )).scalar_one()
            if duplicate_count:
                warnings.warn(
                    "review_finding has duplicate (run_id, key) rows; "
                    "unique index deferred to preserve legacy evidence",
                    RuntimeWarning,
                    stacklevel=2,
                )
            else:
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_review_finding_run_key "
                    "ON review_finding (run_id, key)"
                ))


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


class EscapeRegistry(Base):
    __tablename__ = "escape_registry"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    domain_pack: Mapped[str] = mapped_column(String, index=True, default="cowboy")
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    introduced_at: Mapped[str | None] = mapped_column(String, nullable=True)
    introduced_at_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    root_cause_class: Mapped[str] = mapped_column(String, default="")
    change_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(String, default="")
    postmortem_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    spawned_check: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="open")
    fix_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    missed_by_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Meta(Base):
    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, default="")


class ReviewJob(Base):
    __tablename__ = "review_job"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    change_ref: Mapped[str] = mapped_column(String, index=True)
    repo: Mapped[str] = mapped_column(String, default="node")
    kind: Mapped[str] = mapped_column(String, default="mechanical")   # 'mechanical' | 'deep'
    status: Mapped[str] = mapped_column(String, default="pending")    # pending|running|done|failed
    requested_by: Mapped[str] = mapped_column(String, default="dashboard")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)


class PlannedEvent(Base):
    """Durable plan context used when /plan and /results hit different workers."""
    __tablename__ = "planned_event"
    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    repo: Mapped[str] = mapped_column(String, index=True)
    change_ref: Mapped[str] = mapped_column(String, index=True)
    diff_paths: Mapped[list] = mapped_column(JSON, default=list)
    labels: Mapped[list] = mapped_column(JSON, default=list)
    actor: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ReviewRun(Base):
    """⑧ review trace: 一次对抗式 review 的 provenance — 谁 (host/model) 用哪版
    prompt (skill_rev = marshal checkout 的 git rev) 审了什么。上下文按引用记
    (context_ref: 可重建的 repo@sha + 闭包清单), 不落全量 payload (spec §3.4 的
    "存引用不存载荷" 同一原则)。"""
    __tablename__ = "review_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    change_ref: Mapped[str] = mapped_column(String, index=True)
    repo: Mapped[str] = mapped_column(String, default="")
    mode: Mapped[str] = mapped_column(String, default="regular")   # regular|deep
    host: Mapped[str] = mapped_column(String, default="")          # claude|codex|...
    model: Mapped[str] = mapped_column(String, default="")
    skill_rev: Mapped[str] = mapped_column(String, default="")
    context_ref: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="open", index=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ReviewFinding(Base):
    """⑧ finding 级裁决链: 假说 → skeptic 投票 → quorum → 人类终审。human_verdict
    是唯一的金标注 (accepted|rejected|modified), 由 finding-verdict 命令事后补录;
    空串 = 未终审, 不预填。"""
    __tablename__ = "review_finding"
    __table_args__ = (UniqueConstraint("run_id", "key", name="uq_review_finding_run_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    key: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String, default="")
    claim: Mapped[str] = mapped_column(String, default="")
    location: Mapped[str] = mapped_column(String, default="")
    severity: Mapped[str] = mapped_column(String, default="")
    lens: Mapped[str] = mapped_column(String, default="")
    votes: Mapped[list] = mapped_column(JSON, default=list)
    quorum_verdict: Mapped[str] = mapped_column(String, default="")  # survived|killed|unverified
    human_verdict: Mapped[str] = mapped_column(String, default="")   # ""|accepted|rejected|modified
    human_note: Mapped[str] = mapped_column(String, default="")


class Concept(Base):
    """概念节点缓存 (真相源是 marshal_pack_*/concepts/*.md; 此表单向派生, 只读)。"""
    __tablename__ = "concept"
    id: Mapped[str] = mapped_column(String, primary_key=True)          # = concept_id
    domain_pack: Mapped[str] = mapped_column(String, index=True)
    parent_id: Mapped[str] = mapped_column(String, default="")         # primary_parent; 根为 ""
    importance: Mapped[str] = mapped_column(String, default="low")
    status: Mapped[str] = mapped_column(String, default="draft")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    doc_only: Mapped[bool] = mapped_column(Boolean, default=True)      # H1: 无代码锚定
    definition: Mapped[str] = mapped_column(String, default="")


class ConceptEdge(Base):
    """非树关系 (part_of 多归属 / depends_on 依赖 / conflicts_with)。"""
    __tablename__ = "concept_edge"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    src_id: Mapped[str] = mapped_column(String, index=True)
    dst_id: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)


class ConceptAnchorRow(Base):
    """代码锚点 (H1): 概念声称由某符号实现; verified 由 verify_anchors 回查代码得出。"""
    __tablename__ = "concept_anchor"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    concept_id: Mapped[str] = mapped_column(String, index=True)
    repo: Mapped[str] = mapped_column(String)
    path: Mapped[str] = mapped_column(String)
    symbol: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, default="implements")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


class ConceptChange(Base):
    """概念树每次变更的 provenance (P3 的宝贵数据)。S0 只记 op=add。"""
    __tablename__ = "concept_change"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    change_ref: Mapped[str] = mapped_column(String, index=True)
    op: Mapped[str] = mapped_column(String)     # add|redefine|move|merge|split|rename|deprecate
    concept_id: Mapped[str] = mapped_column(String, index=True)
    before: Mapped[dict] = mapped_column(JSON, default=dict)
    after: Mapped[dict] = mapped_column(JSON, default=dict)
    rationale: Mapped[str] = mapped_column(String, default="")
    actor: Mapped[str] = mapped_column(String, default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
