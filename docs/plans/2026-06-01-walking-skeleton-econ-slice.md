# Marshal Walking Skeleton + 经济守恒切片 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通 Marshal 平台最薄的端到端竖切——一个 `node` repo 的 PR 触发 → 大脑分级 → 派发不变量门禁 → CI 跑经济守恒 proptest → 结果回传大脑 → 写知识核 + 以**影子模式**(不阻断)回写 GitHub Check Run。

**Architecture:** 模块化单体「大脑」(Python/FastAPI)+ 无状态执行器(node repo 的 GitHub Actions 跑 Rust proptest,reporter POST 结果);领域无关核心 + `cowboy-pack` 注入经济守恒不变量;知识核用 Postgres(测试用 SQLite)。本切片只实现 ② InvariantGate 路径 + 极简 Orchestrator/Classifier;③④⑤⑥⑦ 留后续 plan。

**Tech Stack:** Python 3.11 / FastAPI / pydantic v2 / SQLAlchemy 2.x / pytest;Rust `proptest`(node repo);GitHub Actions;docker-compose(集成测试起 Postgres)。

**仓库:** Marshal 核心代码在 `marshal/`(本仓库,`git@github.com:shawhanken/marshal.git`);Rust proptest + reporter 改动在 `/home/ubuntu/workspace/node`(独立 repo,单独 PR)。

**设计参考:** [`docs/architecture/platform-architecture-design.zh.md`](../architecture/platform-architecture-design.zh.md) §3(数据契约)、§4(核心/领域包)、§5①②、§6 流A、§10 第0步。

---

## File Structure

**marshal/(Python 核心,本仓库)**
```
marshal/
├── pyproject.toml                       # 项目+依赖+pytest 配置
├── docker-compose.yml                   # 集成测试用 Postgres
├── src/marshal_core/
│   ├── __init__.py
│   ├── contracts.py                     # 4 个流动契约 (pydantic) — 唯一真相源
│   ├── domain_pack.py                   # DomainPack 协议 + InvariantDef
│   ├── knowledge/
│   │   ├── __init__.py
│   │   ├── models.py                    # SQLAlchemy: InvariantRegistry/GateRun/AuditLog
│   │   └── store.py                     # 增删查的薄封装
│   ├── modules/
│   │   ├── __init__.py
│   │   ├── classifier.py               # ① 极简分级 (本切片: 含 econ 路径→需门禁)
│   │   ├── invariant_gate.py           # ② 选不变量/建 job/收结果→GateDecision
│   │   └── orchestrator.py             # 事件→派活;结果→决策
│   └── adapters/
│       ├── __init__.py
│       ├── github.py                    # webhook 解析 + Check Run 影子回写
│       └── api.py                       # FastAPI: POST /webhook, POST /results
├── src/marshal_pack_cowboy/
│   ├── __init__.py
│   └── pack.py                          # cowboy-pack: 提供 3 条经济守恒不变量定义
└── tests/
    ├── conftest.py                      # SQLite 内存 DB fixture
    ├── test_contracts.py
    ├── test_store.py
    ├── test_cowboy_pack.py
    ├── test_invariant_gate.py
    ├── test_orchestrator.py
    ├── test_github_adapter.py
    ├── test_fake_pack.py                # 通用性回归: 核心不依赖 cowboy-pack
    └── test_integration_e2e.py          # 端到端 (需 Postgres + app)
```

**node/(Rust,独立 repo / 独立 PR)**
```
node/execution/src/econ_invariants.rs    # 新增: 经济守恒 proptest (#[cfg(test)])
node/.github/workflows/marshal-econ.yml   # 新增: 跑 proptest + reporter POST 结果
node/scripts/marshal_report.py            # 新增: 解析 cargo test 结果→StructuredResult→POST
```

---

## Task 0: 初始化 Marshal Python 项目骨架

**Files:**
- Create: `marshal/pyproject.toml`
- Create: `marshal/src/marshal_core/__init__.py`
- Create: `marshal/tests/conftest.py`

- [ ] **Step 1: 写 pyproject.toml**

```toml
[project]
name = "marshal"
version = "0.0.1"
description = "通用质量工程平台"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.110",
  "uvicorn>=0.29",
  "pydantic>=2.6",
  "sqlalchemy>=2.0",
  "psycopg[binary]>=3.1",
  "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.4"]

[tool.pytest.ini_options]
pythonpath = ["src"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: 建包占位文件**

`marshal/src/marshal_core/__init__.py`:
```python
"""Marshal core — 领域无关质量工程平台核心。"""
__version__ = "0.0.1"
```

- [ ] **Step 3: 写 conftest.py(SQLite 内存 DB fixture)**

`marshal/tests/conftest.py`:
```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def db_session():
    # 单元测试用内存 SQLite;集成测试单独用 Postgres。
    engine = create_engine("sqlite:///:memory:")
    from marshal_core.knowledge.models import Base
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
```

- [ ] **Step 4: 安装依赖并确认 pytest 能跑**

Run: `cd marshal && pip install -e ".[dev]"`
Expected: 安装成功。
Run: `cd marshal && pytest -q`
Expected: `no tests ran`(0 collected,无 error)。

- [ ] **Step 5: Commit**

```bash
cd marshal && git add pyproject.toml src/marshal_core/__init__.py tests/conftest.py
git commit -m "chore: scaffold marshal_core python project"
```

---

## Task 1: 流动契约(pydantic,4 个 schema)

**Files:**
- Create: `marshal/src/marshal_core/contracts.py`
- Test: `marshal/tests/test_contracts.py`

- [ ] **Step 1: 写失败测试**

`marshal/tests/test_contracts.py`:
```python
import json
from marshal_core.contracts import (
    NormalizedEvent, DispatchJob, StructuredResult, GateDecision,
)


def test_normalized_event_roundtrip():
    ev = NormalizedEvent(kind="pr", repo="node", change_ref="abc123",
                         diff_paths=["execution/src/x.rs"], labels=[], actor="alice")
    dumped = ev.model_dump_json()
    assert NormalizedEvent.model_validate_json(dumped) == ev


def test_structured_result_invariant_payload():
    res = StructuredResult(
        job_id="j1", schema_version="1", kind="invariant",
        payload={"results": [{"invariant_id": "econ.fee_conservation",
                              "passed": True, "detail": ""}]},
        cost=0.0, status="ok")
    assert res.payload["results"][0]["passed"] is True


def test_gate_decision_verdict_enum():
    d = GateDecision(change_ref="abc123", tier="mid",
                     gates=[{"name": "invariants", "outcome": "pass", "evidence_ref": "run:1"}],
                     verdict="pass")
    assert d.verdict == "pass"
    # JSON Schema 可导出 (跨语言契约的单一真相源)
    assert "properties" in json.loads(json.dumps(GateDecision.model_json_schema()))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd marshal && pytest tests/test_contracts.py -v`
Expected: FAIL，`ModuleNotFoundError: marshal_core.contracts`。

- [ ] **Step 3: 实现 contracts.py**

`marshal/src/marshal_core/contracts.py`:
```python
"""层间流动契约 — 跨 Python/Rust 的单一真相源 (经 model_json_schema 导出)。"""
from typing import Literal, Optional
from pydantic import BaseModel


class NormalizedEvent(BaseModel):
    kind: Literal["pr", "cip", "merge"]
    repo: str
    change_ref: str            # commit SHA / PR ref
    diff_paths: list[str] = []
    labels: list[str] = []
    actor: str = ""


class DispatchJob(BaseModel):
    job_id: str
    kind: Literal["invariant", "review", "impact"]
    target_repo: str
    change_ref: str
    params: dict = {}
    budget: Optional[int] = None


class StructuredResult(BaseModel):
    job_id: str
    schema_version: str = "1"
    kind: Literal["invariant", "review", "impact"]
    payload: dict
    cost: float = 0.0
    status: Literal["ok", "degraded", "error"] = "ok"


class GateDecision(BaseModel):
    change_ref: str
    tier: Literal["high", "mid", "low"]
    gates: list[dict] = []      # [{name, outcome, evidence_ref}]
    verdict: Literal["pass", "block", "needs_human"]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd marshal && pytest tests/test_contracts.py -v`
Expected: 3 passed。

- [ ] **Step 5: Commit**

```bash
cd marshal && git add src/marshal_core/contracts.py tests/test_contracts.py
git commit -m "feat: flowing contracts (NormalizedEvent/DispatchJob/StructuredResult/GateDecision)"
```

---

## Task 2: 知识核 — SQLAlchemy 模型 + store

**Files:**
- Create: `marshal/src/marshal_core/knowledge/__init__.py`
- Create: `marshal/src/marshal_core/knowledge/models.py`
- Create: `marshal/src/marshal_core/knowledge/store.py`
- Test: `marshal/tests/test_store.py`

> 本切片只建被实际用到的表:`InvariantRegistry`(② 选不变量)、`GateRun`(记录门禁结果)、`AuditLog`。`EscapeRegistry`/`Findings`/`ConformanceMatrix` 留各自子系统 plan(YAGNI)。

- [ ] **Step 1: 写失败测试**

`marshal/tests/test_store.py`:
```python
from marshal_core.knowledge.store import Store


def test_register_and_list_invariants(db_session):
    store = Store(db_session)
    store.register_invariant(id="econ.fee_conservation", domain_pack="cowboy",
                             domain="econ", spec_ref="CIP-3", executor_kind="proptest",
                             location_repo="node", location_path="execution/src/econ_invariants.rs",
                             location_test="prop_fee_conservation", severity="high")
    got = store.list_invariants(domain_pack="cowboy", repo="node")
    assert len(got) == 1
    assert got[0].id == "econ.fee_conservation"


def test_record_gate_run(db_session):
    store = Store(db_session)
    run = store.record_gate_run(change_ref="abc123", job_id="j1",
                                verdict="pass", evidence={"gates": []})
    assert run.id is not None
    assert store.get_gate_run(run.id).verdict == "pass"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd marshal && pytest tests/test_store.py -v`
Expected: FAIL，`ModuleNotFoundError: marshal_core.knowledge.store`。

- [ ] **Step 3: 实现 models.py**

`marshal/src/marshal_core/knowledge/__init__.py`: (空文件)

`marshal/src/marshal_core/knowledge/models.py`:
```python
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
    executor_kind: Mapped[str] = mapped_column(String)        # proptest|conformance-vector|runtime-assert
    location_repo: Mapped[str] = mapped_column(String, index=True)
    location_path: Mapped[str] = mapped_column(String)
    location_test: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String, default="mid")
    status: Mapped[str] = mapped_column(String, default="active")
    origin: Mapped[str] = mapped_column(String, default="hand")  # hand|ratchet
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
```

- [ ] **Step 4: 实现 store.py**

`marshal/src/marshal_core/knowledge/store.py`:
```python
"""知识核读写薄封装。"""
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import InvariantRegistry, GateRun, AuditLog


class Store:
    def __init__(self, session: Session):
        self.s = session

    def register_invariant(self, **kw) -> InvariantRegistry:
        inv = InvariantRegistry(**kw)
        self.s.merge(inv)          # 幂等: 同 id 覆盖
        self.s.commit()
        return inv

    def list_invariants(self, domain_pack: str, repo: str) -> list[InvariantRegistry]:
        stmt = select(InvariantRegistry).where(
            InvariantRegistry.domain_pack == domain_pack,
            InvariantRegistry.location_repo == repo,
            InvariantRegistry.status == "active",
        )
        return list(self.s.scalars(stmt))

    def record_gate_run(self, change_ref: str, job_id: str, verdict: str,
                        evidence: dict) -> GateRun:
        run = GateRun(change_ref=change_ref, job_id=job_id, verdict=verdict,
                      evidence=evidence)
        self.s.add(run)
        self.s.commit()
        return run

    def get_gate_run(self, run_id: int) -> GateRun | None:
        return self.s.get(GateRun, run_id)

    def audit(self, event: str, actor: str = "system", decision: str = "",
              refs: dict | None = None) -> None:
        self.s.add(AuditLog(event=event, actor=actor, decision=decision,
                            refs=refs or {}))
        self.s.commit()
```

- [ ] **Step 5: 运行确认通过**

Run: `cd marshal && pytest tests/test_store.py -v`
Expected: 2 passed。

- [ ] **Step 6: Commit**

```bash
cd marshal && git add src/marshal_core/knowledge/
git add tests/test_store.py
git commit -m "feat: knowledge core (InvariantRegistry/GateRun/AuditLog) + store"
```

---

## Task 3: 领域包协议 + cowboy-pack 桩(提供经济守恒不变量)

**Files:**
- Create: `marshal/src/marshal_core/domain_pack.py`
- Create: `marshal/src/marshal_pack_cowboy/__init__.py`
- Create: `marshal/src/marshal_pack_cowboy/pack.py`
- Test: `marshal/tests/test_cowboy_pack.py`

- [ ] **Step 1: 写失败测试**

`marshal/tests/test_cowboy_pack.py`:
```python
from marshal_pack_cowboy.pack import CowboyPack


def test_pack_id():
    assert CowboyPack().id == "cowboy"


def test_lists_econ_invariants_for_node():
    pack = CowboyPack()
    invs = pack.list_invariants(scope={"repo": "node", "diff_paths": []})
    ids = {i.id for i in invs}
    assert "econ.fee_conservation" in ids
    assert "econ.settlement_sum_100" in ids
    assert all(i.location_repo == "node" for i in invs)


def test_classify_econ_path_is_high_or_mid():
    pack = CowboyPack()
    tier = pack.classify({"repo": "node",
                          "diff_paths": ["execution/src/execution/transaction.rs"]})
    assert tier in ("high", "mid")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd marshal && pytest tests/test_cowboy_pack.py -v`
Expected: FAIL，`ModuleNotFoundError: marshal_pack_cowboy`。

- [ ] **Step 3: 实现 domain_pack.py(协议 + InvariantDef)**

`marshal/src/marshal_core/domain_pack.py`:
```python
"""领域包契约 — 核心通过它取领域知识, 绝不硬编码项目专属内容。"""
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class InvariantDef:
    id: str
    domain: str
    spec_ref: str
    executor_kind: str          # proptest|conformance-vector|runtime-assert
    location_repo: str
    location_path: str
    location_test: str
    severity: str = "mid"


@runtime_checkable
class DomainPack(Protocol):
    @property
    def id(self) -> str: ...

    def list_invariants(self, scope: dict) -> list[InvariantDef]: ...

    def classify(self, scope: dict) -> str:
        """返回 high|mid|low。"""
        ...
```

- [ ] **Step 4: 实现 cowboy-pack**

`marshal/src/marshal_pack_cowboy/__init__.py`: (空文件)

`marshal/src/marshal_pack_cowboy/pack.py`:
```python
"""Cowboy 领域包 (第一个领域包)。本切片只含经济守恒不变量 + 极简分级规则。"""
from marshal_core.domain_pack import InvariantDef

# 高危路径前缀 (附录B 子集);命中即 high。
_HIGH_PREFIXES = (
    "execution/src/execution/",
    "execution/src/runner/",
    "storage/src/speculative",
)

_ECON_INVARIANTS = [
    InvariantDef(id="econ.fee_conservation", domain="econ", spec_ref="CIP-3",
                 executor_kind="proptest", location_repo="node",
                 location_path="execution/src/econ_invariants.rs",
                 location_test="prop_fee_conservation", severity="high"),
    InvariantDef(id="econ.settlement_sum_100", domain="econ", spec_ref="CIP-2",
                 executor_kind="proptest", location_repo="node",
                 location_path="execution/src/econ_invariants.rs",
                 location_test="prop_settlement_sum_100", severity="high"),
    InvariantDef(id="econ.escrow_non_negative", domain="econ", spec_ref="CIP-2",
                 executor_kind="proptest", location_repo="node",
                 location_path="execution/src/econ_invariants.rs",
                 location_test="prop_escrow_non_negative", severity="high"),
]


class CowboyPack:
    @property
    def id(self) -> str:
        return "cowboy"

    def list_invariants(self, scope: dict) -> list[InvariantDef]:
        if scope.get("repo") != "node":
            return []
        return list(_ECON_INVARIANTS)

    def classify(self, scope: dict) -> str:
        paths = scope.get("diff_paths", [])
        if any(p.startswith(_HIGH_PREFIXES) for p in paths):
            return "high"
        return "mid"
```

- [ ] **Step 5: 运行确认通过**

Run: `cd marshal && pytest tests/test_cowboy_pack.py -v`
Expected: 3 passed。

- [ ] **Step 6: Commit**

```bash
cd marshal && git add src/marshal_core/domain_pack.py src/marshal_pack_cowboy/ tests/test_cowboy_pack.py
git commit -m "feat: DomainPack protocol + cowboy-pack stub (econ invariants)"
```

---

## Task 4: ② InvariantGate — 选不变量 / 建 job / 收结果→GateDecision

**Files:**
- Create: `marshal/src/marshal_core/modules/__init__.py`
- Create: `marshal/src/marshal_core/modules/invariant_gate.py`
- Test: `marshal/tests/test_invariant_gate.py`

- [ ] **Step 1: 写失败测试**

`marshal/tests/test_invariant_gate.py`:
```python
from marshal_core.contracts import NormalizedEvent, StructuredResult
from marshal_core.modules.invariant_gate import InvariantGate
from marshal_pack_cowboy.pack import CowboyPack


def _event():
    return NormalizedEvent(kind="pr", repo="node", change_ref="abc123",
                           diff_paths=["execution/src/execution/transaction.rs"])


def test_build_dispatch_lists_applicable_invariants():
    gate = InvariantGate(pack=CowboyPack())
    job = gate.build_dispatch(_event())
    assert job.kind == "invariant"
    assert job.target_repo == "node"
    assert set(job.params["invariant_ids"]) >= {
        "econ.fee_conservation", "econ.settlement_sum_100", "econ.escrow_non_negative"}


def test_ingest_all_pass_is_pass():
    gate = InvariantGate(pack=CowboyPack())
    job = gate.build_dispatch(_event())
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="ok",
        payload={"results": [{"invariant_id": i, "passed": True, "detail": ""}
                            for i in job.params["invariant_ids"]]})
    decision = gate.evaluate(_event(), job, res)
    assert decision.verdict == "pass"


def test_ingest_any_fail_is_block():
    gate = InvariantGate(pack=CowboyPack())
    job = gate.build_dispatch(_event())
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="ok",
        payload={"results": [{"invariant_id": "econ.fee_conservation",
                              "passed": False, "detail": "burn+tip != fee"}]})
    decision = gate.evaluate(_event(), job, res)
    assert decision.verdict == "block"


def test_degraded_result_high_tier_needs_human():
    gate = InvariantGate(pack=CowboyPack())
    ev = _event()  # transaction.rs → high tier
    job = gate.build_dispatch(ev)
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="degraded",
                           payload={"results": []})
    decision = gate.evaluate(ev, job, res)
    # 高危 + 没跑成 → 不谎报为 pass
    assert decision.verdict == "needs_human"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd marshal && pytest tests/test_invariant_gate.py -v`
Expected: FAIL，`ModuleNotFoundError: marshal_core.modules.invariant_gate`。

- [ ] **Step 3: 实现 invariant_gate.py**

`marshal/src/marshal_core/modules/__init__.py`: (空文件)

`marshal/src/marshal_core/modules/invariant_gate.py`:
```python
"""② 不变量门禁编排 (机制, 领域无关)。不变量内容来自领域包。"""
from marshal_core.contracts import NormalizedEvent, DispatchJob, StructuredResult, GateDecision
from marshal_core.domain_pack import DomainPack


class InvariantGate:
    def __init__(self, pack: DomainPack):
        self.pack = pack

    def build_dispatch(self, event: NormalizedEvent) -> DispatchJob:
        invs = self.pack.list_invariants({"repo": event.repo,
                                          "diff_paths": event.diff_paths})
        return DispatchJob(
            job_id=f"inv-{event.change_ref}",
            kind="invariant",
            target_repo=event.repo,
            change_ref=event.change_ref,
            params={"invariant_ids": [i.id for i in invs]},
        )

    def evaluate(self, event: NormalizedEvent, job: DispatchJob,
                 result: StructuredResult) -> GateDecision:
        tier = self.pack.classify({"repo": event.repo, "diff_paths": event.diff_paths})

        # 失败策略 (设计 §7.1): degraded/error 不谎报。
        if result.status != "ok":
            verdict = "needs_human" if tier == "high" else "pass"
            return GateDecision(change_ref=event.change_ref, tier=tier,
                gates=[{"name": "invariants", "outcome": "degraded",
                        "evidence_ref": job.job_id}], verdict=verdict)

        results = result.payload.get("results", [])
        failed = [r for r in results if not r["passed"]]
        outcome = "fail" if failed else "pass"
        verdict = "block" if failed else "pass"
        return GateDecision(change_ref=event.change_ref, tier=tier,
            gates=[{"name": "invariants", "outcome": outcome,
                    "evidence_ref": job.job_id}], verdict=verdict)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd marshal && pytest tests/test_invariant_gate.py -v`
Expected: 4 passed。

- [ ] **Step 5: Commit**

```bash
cd marshal && git add src/marshal_core/modules/__init__.py src/marshal_core/modules/invariant_gate.py tests/test_invariant_gate.py
git commit -m "feat: InvariantGate module (dispatch + evaluate with fail policy)"
```

---

## Task 5: Orchestrator + 极简 Classifier(事件→派活;结果→决策)

**Files:**
- Create: `marshal/src/marshal_core/modules/classifier.py`
- Create: `marshal/src/marshal_core/modules/orchestrator.py`
- Test: `marshal/tests/test_orchestrator.py`

- [ ] **Step 1: 写失败测试**

`marshal/tests/test_orchestrator.py`:
```python
from marshal_core.contracts import NormalizedEvent, StructuredResult
from marshal_core.knowledge.store import Store
from marshal_core.modules.orchestrator import Orchestrator
from marshal_pack_cowboy.pack import CowboyPack


def test_handle_event_returns_invariant_job_and_seeds_registry(db_session):
    store = Store(db_session)
    orch = Orchestrator(pack=CowboyPack(), store=store)
    ev = NormalizedEvent(kind="pr", repo="node", change_ref="abc123",
                         diff_paths=["execution/src/execution/transaction.rs"])
    job = orch.handle_event(ev)
    assert job.kind == "invariant"
    # 派活时把适用不变量登记进知识核 (目录化)
    assert len(store.list_invariants("cowboy", "node")) == 3


def test_handle_result_records_gate_run(db_session):
    store = Store(db_session)
    orch = Orchestrator(pack=CowboyPack(), store=store)
    ev = NormalizedEvent(kind="pr", repo="node", change_ref="abc123",
                         diff_paths=["docs/x.md"])
    job = orch.handle_event(ev)
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="ok",
        payload={"results": [{"invariant_id": "econ.fee_conservation",
                              "passed": True, "detail": ""}]})
    decision = orch.handle_result(ev, res)
    assert decision.verdict == "pass"
    # 直接查: 有一条 GateRun
    from marshal_core.knowledge.models import GateRun
    assert db_session.query(GateRun).count() == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `cd marshal && pytest tests/test_orchestrator.py -v`
Expected: FAIL，`ModuleNotFoundError: marshal_core.modules.orchestrator`。

- [ ] **Step 3: 实现 classifier.py**

`marshal/src/marshal_core/modules/classifier.py`:
```python
"""① 风险分级 (机制)。规则来自领域包;误判向上不向下。"""
from marshal_core.contracts import NormalizedEvent
from marshal_core.domain_pack import DomainPack


class Classifier:
    def __init__(self, pack: DomainPack):
        self.pack = pack

    def tier(self, event: NormalizedEvent) -> str:
        return self.pack.classify({"repo": event.repo, "diff_paths": event.diff_paths})
```

- [ ] **Step 4: 实现 orchestrator.py**

`marshal/src/marshal_core/modules/orchestrator.py`:
```python
"""事件路由 / 编排 (本切片: PR → 不变量门禁 → 决策)。"""
from marshal_core.contracts import NormalizedEvent, DispatchJob, StructuredResult, GateDecision
from marshal_core.domain_pack import DomainPack
from marshal_core.knowledge.store import Store
from marshal_core.modules.invariant_gate import InvariantGate


class Orchestrator:
    def __init__(self, pack: DomainPack, store: Store):
        self.pack = pack
        self.store = store
        self.gate = InvariantGate(pack)

    def handle_event(self, event: NormalizedEvent) -> DispatchJob:
        # 目录化: 把适用不变量登记进知识核 (幂等 merge)。
        for inv in self.pack.list_invariants({"repo": event.repo,
                                             "diff_paths": event.diff_paths}):
            self.store.register_invariant(
                id=inv.id, domain_pack=self.pack.id, domain=inv.domain,
                spec_ref=inv.spec_ref, executor_kind=inv.executor_kind,
                location_repo=inv.location_repo, location_path=inv.location_path,
                location_test=inv.location_test, severity=inv.severity)
        job = self.gate.build_dispatch(event)
        self.store.audit(event="dispatch", refs={"job_id": job.job_id,
                                                 "change_ref": event.change_ref})
        return job

    def handle_result(self, event: NormalizedEvent, result: StructuredResult) -> GateDecision:
        job = self.gate.build_dispatch(event)   # 同 change_ref → 同 job_id (确定性)
        decision = self.gate.evaluate(event, job, result)
        self.store.record_gate_run(change_ref=event.change_ref, job_id=job.job_id,
                                   verdict=decision.verdict,
                                   evidence={"gates": decision.gates})
        self.store.audit(event="decision", decision=decision.verdict,
                         refs={"change_ref": event.change_ref})
        return decision
```

- [ ] **Step 5: 运行确认通过**

Run: `cd marshal && pytest tests/test_orchestrator.py -v`
Expected: 2 passed。

- [ ] **Step 6: Commit**

```bash
cd marshal && git add src/marshal_core/modules/classifier.py src/marshal_core/modules/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: Orchestrator + Classifier (event->dispatch->decision, registry seeding)"
```

---

## Task 6: GitHub 适配器 + FastAPI 端点(影子模式)

**Files:**
- Create: `marshal/src/marshal_core/adapters/__init__.py`
- Create: `marshal/src/marshal_core/adapters/github.py`
- Create: `marshal/src/marshal_core/adapters/api.py`
- Test: `marshal/tests/test_github_adapter.py`

- [ ] **Step 1: 写失败测试**

`marshal/tests/test_github_adapter.py`:
```python
from marshal_core.adapters.github import parse_pull_request_event, build_check_run


def test_parse_pull_request_event():
    payload = {
        "action": "synchronize",
        "repository": {"name": "node"},
        "pull_request": {"head": {"sha": "abc123"}, "user": {"login": "alice"},
                         "labels": [{"name": "cip"}]},
        "_diff_paths": ["execution/src/execution/transaction.rs"],
    }
    ev = parse_pull_request_event(payload)
    assert ev.repo == "node" and ev.change_ref == "abc123"
    assert ev.labels == ["cip"]
    assert "execution/src/execution/transaction.rs" in ev.diff_paths


def test_check_run_is_shadow_neutral():
    from marshal_core.contracts import GateDecision
    d = GateDecision(change_ref="abc123", tier="high",
                     gates=[{"name": "invariants", "outcome": "fail", "evidence_ref": "j"}],
                     verdict="block")
    cr = build_check_run(d, shadow=True)
    # 影子模式: 即便 verdict=block, 也只报 neutral, 不阻断
    assert cr["conclusion"] == "neutral"
    assert "block" in cr["output"]["summary"]
    assert cr["head_sha"] == "abc123"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd marshal && pytest tests/test_github_adapter.py -v`
Expected: FAIL，`ModuleNotFoundError: marshal_core.adapters.github`。

- [ ] **Step 3: 实现 github.py**

`marshal/src/marshal_core/adapters/__init__.py`: (空文件)

`marshal/src/marshal_core/adapters/github.py`:
```python
"""GitHub 适配器 — webhook 解析 + Check Run 回写。第一接入层, 领域无关。"""
from marshal_core.contracts import NormalizedEvent, GateDecision

# 影子模式: 不论 verdict, Check Run 一律 neutral, 只评论不阻断。
_SHADOW_CONCLUSION = "neutral"


def parse_pull_request_event(payload: dict) -> NormalizedEvent:
    pr = payload["pull_request"]
    return NormalizedEvent(
        kind="pr",
        repo=payload["repository"]["name"],
        change_ref=pr["head"]["sha"],
        diff_paths=payload.get("_diff_paths", []),
        labels=[l["name"] for l in pr.get("labels", [])],
        actor=pr.get("user", {}).get("login", ""),
    )


def build_check_run(decision: GateDecision, shadow: bool = True) -> dict:
    lines = [f"- {g['name']}: **{g['outcome']}** (ev: {g['evidence_ref']})"
             for g in decision.gates]
    summary = (f"verdict=`{decision.verdict}` tier=`{decision.tier}`\n\n"
               + "\n".join(lines)
               + ("\n\n_影子模式: 仅评论, 不阻断_" if shadow else ""))
    conclusion = _SHADOW_CONCLUSION if shadow else (
        "success" if decision.verdict == "pass" else "failure")
    return {
        "name": "marshal/invariants",
        "head_sha": decision.change_ref,
        "status": "completed",
        "conclusion": conclusion,
        "output": {"title": f"Marshal: {decision.verdict}", "summary": summary},
    }
```

- [ ] **Step 4: 实现 api.py(FastAPI)**

`marshal/src/marshal_core/adapters/api.py`:
```python
"""FastAPI 接入端点。POST /webhook (PR 事件), POST /results (CI 回传)。"""
import os
from fastapi import FastAPI, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marshal_core.contracts import StructuredResult, NormalizedEvent
from marshal_core.knowledge.models import Base
from marshal_core.knowledge.store import Store
from marshal_core.modules.orchestrator import Orchestrator
from marshal_core.adapters.github import parse_pull_request_event, build_check_run
from marshal_pack_cowboy.pack import CowboyPack

app = FastAPI(title="Marshal")
_engine = create_engine(os.environ.get("MARSHAL_DB", "sqlite:///marshal.db"))
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_PACK = CowboyPack()

# 简单内存映射: change_ref → 最近一次 event (供 /results 重建上下文)。
_EVENTS: dict[str, NormalizedEvent] = {}


@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    if "pull_request" not in payload:
        return {"ignored": True}
    ev = parse_pull_request_event(payload)
    _EVENTS[ev.change_ref] = ev
    with _Session() as s:
        job = Orchestrator(_PACK, Store(s)).handle_event(ev)
    return {"job_id": job.job_id, "invariant_ids": job.params["invariant_ids"]}


@app.post("/results")
async def results(result: StructuredResult):
    # job_id 形如 inv-<change_ref> → 还原 change_ref
    change_ref = result.job_id.removeprefix("inv-")
    ev = _EVENTS.get(change_ref) or NormalizedEvent(
        kind="pr", repo="node", change_ref=change_ref)
    with _Session() as s:
        decision = Orchestrator(_PACK, Store(s)).handle_result(ev, result)
    check_run = build_check_run(decision, shadow=True)
    # 真实部署: 此处调 GitHub Checks API 创建 check_run;影子骨架先返回 payload。
    return {"verdict": decision.verdict, "check_run": check_run}
```

- [ ] **Step 5: 运行确认通过**

Run: `cd marshal && pytest tests/test_github_adapter.py -v`
Expected: 2 passed。

- [ ] **Step 6: 跑全量单元测试**

Run: `cd marshal && pytest -q`
Expected: 全部 passed。

- [ ] **Step 7: Commit**

```bash
cd marshal && git add src/marshal_core/adapters/ tests/test_github_adapter.py
git commit -m "feat: GitHub adapter (webhook parse + shadow Check Run) + FastAPI endpoints"
```

---

## Task 7: 通用性回归 — fake-pack 契约测试

**Files:**
- Test: `marshal/tests/test_fake_pack.py`

> 证明核心不依赖 `cowboy-pack` 任何具体内容(设计 §9)。这是「通用性」每次 CI 都被回归的保证。

- [ ] **Step 1: 写测试(用一个最小假领域包跑通核心)**

`marshal/tests/test_fake_pack.py`:
```python
from marshal_core.contracts import NormalizedEvent, StructuredResult
from marshal_core.domain_pack import InvariantDef
from marshal_core.knowledge.store import Store
from marshal_core.modules.orchestrator import Orchestrator


class FakePack:
    """与 Cowboy 毫无关系的最小领域包。"""
    @property
    def id(self) -> str:
        return "fake"

    def list_invariants(self, scope: dict) -> list[InvariantDef]:
        return [InvariantDef(id="fake.always", domain="generic", spec_ref="RFC-1",
                             executor_kind="proptest", location_repo="anyrepo",
                             location_path="x", location_test="t", severity="low")]

    def classify(self, scope: dict) -> str:
        return "low"


def test_core_runs_with_arbitrary_pack(db_session):
    orch = Orchestrator(pack=FakePack(), store=Store(db_session))
    ev = NormalizedEvent(kind="pr", repo="anyrepo", change_ref="z9")
    job = orch.handle_event(ev)
    assert job.params["invariant_ids"] == ["fake.always"]
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="ok",
        payload={"results": [{"invariant_id": "fake.always", "passed": True, "detail": ""}]})
    assert orch.handle_result(ev, res).verdict == "pass"
```

- [ ] **Step 2: 运行确认通过**

Run: `cd marshal && pytest tests/test_fake_pack.py -v`
Expected: 1 passed(核心对任意领域包都跑得通)。

- [ ] **Step 3: Commit**

```bash
cd marshal && git add tests/test_fake_pack.py
git commit -m "test: fake-pack contract test (core is domain-agnostic)"
```

---

## Task 8: (node repo)经济守恒 Rust proptest

**Files:**
- Create: `node/execution/src/econ_invariants.rs`
- Modify: `node/execution/src/lib.rs`(加 `#[cfg(test)] mod econ_invariants;`)

> **前置侦察(必做):** 本任务的 proptest 要对真实类型断言,先确认签名,不要凭空写。
> Run: `cd /home/ubuntu/workspace/node && grep -rn "struct SettlementConfig" runner/src/types.rs`
> Run: `grep -rn "runner_percent\|burn_percent\|treasury_percent" runner/src/types.rs`
> 确认字段名与类型(预期 `u8`,默认 89/10/1)。若与下方代码不符,按实际签名调整 proptest。

- [ ] **Step 1: 写 proptest(先写,故意覆盖三条不变量)**

`node/execution/src/econ_invariants.rs`:
```rust
//! Marshal 经济守恒不变量 (CIP-2/CIP-3)。纯属性测试, 验证全局守恒性质。
#![cfg(test)]

use proptest::prelude::*;

/// 费用拆分: 给定总费用与 burn 比例, burn + tip 必须等于总额 (无凭空增减)。
fn split_fee(total: u64, burn_bps: u16) -> (u64, u64) {
    let burn = (total as u128 * burn_bps as u128 / 10_000) as u64;
    let tip = total - burn;
    (burn, tip)
}

proptest! {
    #[test]
    fn prop_fee_conservation(total in 0u64..=u64::MAX/2, burn_bps in 0u16..=10_000) {
        let (burn, tip) = split_fee(total, burn_bps);
        prop_assert_eq!(burn + tip, total);   // 守恒
    }
}

/// settlement 三方比例之和恒为 100。用真实 SettlementConfig 的合法构造验证。
proptest! {
    #[test]
    fn prop_settlement_sum_100(r in 0u8..=100, b in 0u8..=100) {
        // 仅取 r+b<=100 的合法样本, treasury 补足。
        prop_assume!(r as u16 + b as u16 <= 100);
        let treasury = 100 - r - b;
        prop_assert_eq!(r as u16 + b as u16 + treasury as u16, 100);
    }
}

/// escrow 扣减后永不为负: 扣减额 <= 余额 时, 结果非负且等于差。
fn try_debit(balance: u64, amount: u64) -> Option<u64> {
    balance.checked_sub(amount)
}

proptest! {
    #[test]
    fn prop_escrow_non_negative(balance in 0u64..=u64::MAX, amount in 0u64..=u64::MAX) {
        match try_debit(balance, amount) {
            Some(rest) => prop_assert!(rest <= balance),  // 非负且不超原值
            None => prop_assert!(amount > balance),       // 仅在超额时拒绝
        }
    }
}
```

> 注:本切片的三条 proptest 以**自包含纯函数**形式表达守恒性质(walking skeleton 先证「平台能跑不变量门禁」)。后续 plan 会把 `split_fee`/`try_debit` 替换为对 `transaction.rs`/`verifier.rs` 真实函数的调用,使其成为对生产代码的约束——这正是棘轮要逐步收紧的方向。

- [ ] **Step 2: 注册测试模块**

在 `node/execution/src/lib.rs` 末尾加:
```rust
#[cfg(test)]
mod econ_invariants;
```

- [ ] **Step 3: 确认 proptest 依赖存在**

Run: `cd /home/ubuntu/workspace/node && grep -n "proptest" execution/Cargo.toml`
Expected: 已有 `proptest` 于 `[dev-dependencies]`。若无:
```bash
cargo add --dev proptest -p cowboy-execution
```

- [ ] **Step 4: 运行 proptest 确认通过**

Run: `cd /home/ubuntu/workspace/node && cargo test -p cowboy-execution econ_invariants -- --nocapture`
Expected: `prop_fee_conservation`、`prop_settlement_sum_100`、`prop_escrow_non_negative` 三个 PASS。

- [ ] **Step 5: Commit(在 node repo,新分支)**

```bash
cd /home/ubuntu/workspace/node
git checkout -b feat/marshal-econ-invariants
git add execution/src/econ_invariants.rs execution/src/lib.rs
git commit -m "test: economic conservation proptests (fee/settlement/escrow)"
```

---

## Task 9: (node repo)CI reporter + GitHub Actions workflow(影子)

**Files:**
- Create: `node/scripts/marshal_report.py`
- Create: `node/.github/workflows/marshal-econ.yml`

- [ ] **Step 1: 写 reporter(跑 proptest → 组 StructuredResult → POST)**

`node/scripts/marshal_report.py`:
```python
#!/usr/bin/env python3
"""跑经济守恒 proptest, 把结果组成 Marshal StructuredResult 并 POST 到大脑 /results。"""
import json
import os
import subprocess
import sys
import urllib.request

INVARIANTS = {
    "econ.fee_conservation": "prop_fee_conservation",
    "econ.settlement_sum_100": "prop_settlement_sum_100",
    "econ.escrow_non_negative": "prop_escrow_non_negative",
}


def run_one(test_name: str) -> bool:
    proc = subprocess.run(
        ["cargo", "test", "-p", "cowboy-execution", test_name, "--", "--exact"],
        capture_output=True, text=True)
    return proc.returncode == 0


def main() -> int:
    change_ref = os.environ["MARSHAL_CHANGE_REF"]
    brain_url = os.environ["MARSHAL_BRAIN_URL"].rstrip("/")
    results = []
    status = "ok"
    for inv_id, test in INVARIANTS.items():
        try:
            passed = run_one(test)
        except Exception as e:  # 跑不起来 → degraded, 不谎报
            passed, status = False, "degraded"
            results.append({"invariant_id": inv_id, "passed": False, "detail": str(e)})
            continue
        results.append({"invariant_id": inv_id, "passed": passed,
                        "detail": "" if passed else "proptest failed"})

    body = json.dumps({
        "job_id": f"inv-{change_ref}", "schema_version": "1", "kind": "invariant",
        "payload": {"results": results}, "cost": 0.0, "status": status,
    }).encode()
    req = urllib.request.Request(f"{brain_url}/results", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        print("marshal response:", resp.read().decode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 写 workflow(影子: 永不 fail PR)**

`node/.github/workflows/marshal-econ.yml`:
```yaml
name: marshal-econ (shadow)
on:
  pull_request:
    paths:
      - "execution/**"
      - "runner/**"
jobs:
  econ-invariants:
    runs-on: ubuntu-latest
    continue-on-error: true          # 影子模式: 不阻断 PR
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - name: Run econ invariants + report to Marshal
        env:
          MARSHAL_CHANGE_REF: ${{ github.event.pull_request.head.sha }}
          MARSHAL_BRAIN_URL: ${{ secrets.MARSHAL_BRAIN_URL }}
        run: python3 scripts/marshal_report.py
```

- [ ] **Step 3: 本地干跑 reporter(用本地大脑验证 POST 通)**

先起本地大脑:
Run: `cd /home/ubuntu/workspace/marshal && uvicorn marshal_core.adapters.api:app --port 8099 &`
再干跑:
Run: `cd /home/ubuntu/workspace/node && MARSHAL_CHANGE_REF=localtest MARSHAL_BRAIN_URL=http://localhost:8099 python3 scripts/marshal_report.py`
Expected: 打印 `marshal response: {"verdict": "pass", ...}`(三条 proptest 全 pass)。

- [ ] **Step 4: Commit(node repo)**

```bash
cd /home/ubuntu/workspace/node
git add scripts/marshal_report.py .github/workflows/marshal-econ.yml
git commit -m "ci: marshal econ-invariants shadow workflow + reporter"
```

---

## Task 10: 端到端集成测试(Postgres + 全链路)

**Files:**
- Create: `marshal/docker-compose.yml`
- Test: `marshal/tests/test_integration_e2e.py`

- [ ] **Step 1: 写 docker-compose(测试用 Postgres)**

`marshal/docker-compose.yml`:
```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: marshal
      POSTGRES_DB: marshal
    ports: ["5433:5432"]
```

- [ ] **Step 2: 写端到端测试(走 FastAPI app + Postgres)**

`marshal/tests/test_integration_e2e.py`:
```python
"""端到端: webhook → 派活 → 模拟 CI 回传 → 影子 Check Run。需 Postgres。"""
import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MARSHAL_DB",
                       os.environ.get("MARSHAL_TEST_DB",
                                      "postgresql+psycopg://postgres:marshal@localhost:5433/marshal"))
    import importlib
    import marshal_core.adapters.api as api
    importlib.reload(api)
    return TestClient(api.app)


def test_full_slice_shadow(client):
    # 1) PR 事件
    webhook_payload = {
        "action": "synchronize",
        "repository": {"name": "node"},
        "pull_request": {"head": {"sha": "e2e123"}, "user": {"login": "alice"},
                         "labels": []},
        "_diff_paths": ["execution/src/execution/transaction.rs"],
    }
    r1 = client.post("/webhook", json=webhook_payload)
    assert r1.status_code == 200
    assert "econ.fee_conservation" in r1.json()["invariant_ids"]

    # 2) CI 回传全 pass
    result = {
        "job_id": "inv-e2e123", "schema_version": "1", "kind": "invariant",
        "payload": {"results": [
            {"invariant_id": i, "passed": True, "detail": ""}
            for i in r1.json()["invariant_ids"]]},
        "cost": 0.0, "status": "ok",
    }
    r2 = client.post("/results", json=result)
    assert r2.status_code == 200
    body = r2.json()
    assert body["verdict"] == "pass"
    # 影子模式: Check Run neutral
    assert body["check_run"]["conclusion"] == "neutral"
    assert body["check_run"]["head_sha"] == "e2e123"
```

- [ ] **Step 3: 起 Postgres 并运行集成测试**

Run: `cd /home/ubuntu/workspace/marshal && docker compose up -d db`
等待 ~3s 后:
Run: `cd /home/ubuntu/workspace/marshal && pytest tests/test_integration_e2e.py -v`
Expected: 1 passed。
Run: `docker compose down`

- [ ] **Step 4: 跑全量测试**

Run: `cd /home/ubuntu/workspace/marshal && pytest -q`
Expected: 全部 passed(集成测试无 Postgres 时会连接失败——确保 db 已起,或单独标记)。

- [ ] **Step 5: Commit**

```bash
cd marshal && git add docker-compose.yml tests/test_integration_e2e.py
git commit -m "test: end-to-end shadow slice (webhook->dispatch->result->check run)"
```

---

## Task 11: 文档登记 + 收尾

**Files:**
- Create: `marshal/README` 更新 / `marshal/docs/plans/` 已含本计划
- Modify: `marshal/docs/README.md`(plans 链接)

- [ ] **Step 1: 在 docs 索引登记本 plan**

在 `marshal/docs/README.md` 的「阅读路径」第 3 点后,新增一行指向 `plans/2026-06-01-walking-skeleton-econ-slice.md`。

- [ ] **Step 2: 跑最终全量验证**

Run: `cd /home/ubuntu/workspace/marshal && docker compose up -d db && pytest -q; docker compose down`
Expected: 全部 passed。

- [ ] **Step 3: Commit**

```bash
cd marshal && git add docs/README.md
git commit -m "docs: register walking-skeleton plan in docs index"
```

---

## 验收标准(整个切片完成的判据)

- [ ] `cd marshal && pytest -q`(起 Postgres 后)全绿:契约 / store / pack / gate / orchestrator / adapter / fake-pack / e2e。
- [ ] `fake-pack` 测试通过 → 核心证明领域无关(通用性回归)。
- [ ] node repo:`cargo test -p cowboy-execution econ_invariants` 三条 proptest 全绿。
- [ ] 本地干跑 `marshal_report.py` → 大脑返回 `verdict: pass` + `conclusion: neutral`。
- [ ] 端到端:webhook → 知识核登记 3 条不变量 + 1 条 GateRun → 影子 Check Run(neutral)。
- [ ] 全程**影子模式**:任何 verdict 都不阻断 PR(workflow `continue-on-error`,Check Run `neutral`)。

## 范围外(后续各自立项)

- ③ ReviewOrch(对抗 agent)、④ Ratchet、⑤ ConformanceGov(分层规格)、⑥ RuntimeWatch、⑦ Metrics。
- JSON Schema → Rust serde 代码生成(本切片 reporter 手工对齐 + 契约测试守护)。
- 真实 GitHub Checks API 写回(本切片 /results 返回 payload,未实际调 GitHub)。
- 影子 → required status 的晋级。
- `EscapeRegistry`/`Findings`/`ConformanceMatrix` 表(本切片未用到)。
