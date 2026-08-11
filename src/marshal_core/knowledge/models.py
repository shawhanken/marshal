"""知识核持久模型 — schema 领域无关 (domain/severity 取值由领域包定义)。"""
from datetime import datetime, timezone
from sqlalchemy import String, Integer, JSON, DateTime, Boolean, Float
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


class EscapeRegistry(Base):
    __tablename__ = "escape_registry"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    domain_pack: Mapped[str] = mapped_column(String, index=True, default="cowboy")
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    introduced_at: Mapped[str | None] = mapped_column(String, nullable=True)
    root_cause_class: Mapped[str] = mapped_column(String, default="")
    change_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(String, default="")
    postmortem_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    spawned_check: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="open")
    fix_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    missed_by_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ReviewFinding(Base):
    """⑧ finding 级裁决链: 假说 → skeptic 投票 → quorum → 人类终审。human_verdict
    是唯一的金标注 (accepted|rejected|modified), 由 finding-verdict 命令事后补录;
    空串 = 未终审, 不预填。"""
    __tablename__ = "review_finding"
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
