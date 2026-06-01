# Marshal Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Marshal 平台建成前,落地一个 Claude Code skill「marshal」——对当前分支/PR diff 跑 ① 分级 + ③ 对抗式 review + ④ 逃逸棘轮的认知闭环,跨 repo 契约感知,文件态(复用 marshal SQLite 知识核),建议态(不硬阻断)。

**Architecture:** skill 做判断性编排(住 `marshal/.claude/skills/`,软链到 `~/.claude/skills/`);确定性逻辑外包给 marshal 薄 CLI(`python -m marshal_core.cli`),CLI 读 `marshal_pack_cowboy` 领域真相、读写绝对路径的 `$MARSHAL_HOME/marshal.db`。

**Tech Stack:** Python 3.11+ / SQLAlchemy 2.0 / pytest;skill 复用 `/code-review ultra`。

**源 spec:** `docs/superpowers/specs/2026-06-01-marshal-skill-design.md`

**测试命令(贯穿全程):** 在 `/home/ubuntu/workspace/marshal` 下跑 `.venv/bin/python -m pytest -q`(`pyproject` 已设 `pythonpath=["src"]`;`conftest.py` 提供内存 SQLite `db_session` fixture)。

---

## 文件结构

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/marshal_core/knowledge/models.py` | +`EscapeRegistry` 表 | Modify |
| `src/marshal_core/knowledge/store.py` | +`open_escape`/`close_escape`/`list_open_escapes` | Modify |
| `src/marshal_pack_cowboy/pack.py` | +`Contract`/`CONTRACTS`/`contracts_hit`/`classify_detailed`/`REVIEW_DIMENSIONS`/`review_plan`/跨 repo `list_invariants` | Modify |
| `src/marshal_core/cli.py` | 薄 CLI: `_db_url` + argparse + 6 子命令 | Create |
| `tests/test_escape_registry.py` | EscapeRegistry + Store 棘轮约束 | Create |
| `tests/test_cowboy_contracts.py` | 契约拓扑 + 跨 repo 分级/选不变量 | Create |
| `tests/test_cowboy_classify.py` | 分级细化 + review_plan | Create |
| `tests/test_cli.py` | CLI JSON 出入 + 错误退出码 | Create |
| `.claude/skills/marshal/SKILL.md` | 主流程(流 A/流 C 编排)+ 自检 | Create |
| `.claude/skills/marshal/references/gate-flow.md` | 门禁评估细节 | Create |
| `.claude/skills/marshal/references/ratchet-flow.md` | 棘轮细节 | Create |
| `.claude/skills/marshal/references/review-orchestration.md` | ③ 多视角编排 | Create |

> **不破坏既有契约:** `DomainPack.classify(scope) -> str` 协议不变(被 `Classifier`/`InvariantGate`/既有测试使用)。新增 `classify_detailed` 等方法;`classify` 退化为取 `classify_detailed(...)["tier"]`。CLI 直接实例化 `CowboyPack`(CLI 是 marshal 侧胶水,允许知道具体包)。

---

## Task 1: EscapeRegistry 表 + Store 棘轮方法

棘轮的持久地基。硬约束: `close_escape` 缺 `spawned_check` 必须 raise。

**Files:**
- Modify: `src/marshal_core/knowledge/models.py`
- Modify: `src/marshal_core/knowledge/store.py`
- Test: `tests/test_escape_registry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_escape_registry.py`:

```python
import pytest
from marshal_core.knowledge.store import Store


def test_open_escape_creates_open_entry(db_session):
    store = Store(db_session)
    esc = store.open_escape(id="esc-0001", description="bare 2**10000 绕过 int guard",
                            root_cause_class="determinism-gap")
    assert esc.id == "esc-0001"
    assert esc.status == "open"
    assert esc.spawned_check is None
    assert store.list_open_escapes()[0].id == "esc-0001"


def test_close_escape_sets_spawned_check_and_status(db_session):
    store = Store(db_session)
    store.open_escape(id="esc-0002", description="d", root_cause_class="c")
    store.close_escape("esc-0002", spawned_check="det.bare_pow_literal")
    esc = store.get_escape("esc-0002")
    assert esc.status == "closed"
    assert esc.spawned_check == "det.bare_pow_literal"
    assert store.list_open_escapes() == []


def test_close_escape_without_spawned_check_raises(db_session):
    store = Store(db_session)
    store.open_escape(id="esc-0003", description="d", root_cause_class="c")
    with pytest.raises(ValueError, match="spawned_check"):
        store.close_escape("esc-0003", spawned_check="")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_escape_registry.py -q`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'open_escape'`

- [ ] **Step 3: Add the EscapeRegistry model**

In `src/marshal_core/knowledge/models.py`, after the `AuditLog` class append:

```python
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
```

- [ ] **Step 4: Add the Store methods**

In `src/marshal_core/knowledge/store.py`, update the import line and add three methods.

Change the import at top:

```python
from .models import InvariantRegistry, GateRun, AuditLog, EscapeRegistry
```

Add inside class `Store` (after `audit`):

```python
    def open_escape(self, **kw) -> EscapeRegistry:
        esc = EscapeRegistry(**kw)
        self.s.add(esc)
        self.s.commit()
        return esc

    def get_escape(self, escape_id: str) -> EscapeRegistry | None:
        return self.s.get(EscapeRegistry, escape_id)

    def list_open_escapes(self) -> list[EscapeRegistry]:
        from sqlalchemy import select
        stmt = select(EscapeRegistry).where(EscapeRegistry.status == "open")
        return list(self.s.scalars(stmt))

    def close_escape(self, escape_id: str, spawned_check: str) -> EscapeRegistry:
        if not spawned_check:
            raise ValueError("cannot close escape without a spawned_check (棘轮纪律)")
        esc = self.s.get(EscapeRegistry, escape_id)
        if esc is None:
            raise ValueError(f"escape not found: {escape_id}")
        esc.spawned_check = spawned_check
        esc.status = "closed"
        self.s.commit()
        return esc
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_escape_registry.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Run full suite (no regressions)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (26 passed)

- [ ] **Step 7: Commit**

```bash
git add src/marshal_core/knowledge/models.py src/marshal_core/knowledge/store.py tests/test_escape_registry.py
git commit -m "feat(knowledge): add EscapeRegistry + ratchet store methods"
```

---

## Task 2: cowboy-pack 契约拓扑 + contracts_hit

跨 repo 契约感知的数据与命中逻辑(B 核心)。

**Files:**
- Modify: `src/marshal_pack_cowboy/pack.py`
- Test: `tests/test_cowboy_contracts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cowboy_contracts.py`:

```python
from marshal_pack_cowboy.pack import CowboyPack


def test_wallet_tx_encoding_change_hits_contract():
    pack = CowboyPack()
    hit = pack.contracts_hit({"repo": "wallet",
                              "diff_paths": ["src/tx/encode.js"]})
    assert "tx-encoding" in hit


def test_node_transaction_change_hits_tx_contract():
    pack = CowboyPack()
    hit = pack.contracts_hit({"repo": "node",
                              "diff_paths": ["types/src/transaction.rs"]})
    assert "tx-encoding" in hit


def test_runner_types_change_hits_runner_contract():
    pack = CowboyPack()
    hit = pack.contracts_hit({"repo": "runner",
                              "diff_paths": ["crates/runner-common/src/types.rs"]})
    assert "runner-types" in hit


def test_unrelated_change_hits_nothing():
    pack = CowboyPack()
    hit = pack.contracts_hit({"repo": "node",
                              "diff_paths": ["rpc/src/handlers.rs"]})
    assert hit == []
```

> 注: `wallet` 的 tx 编码路径前缀 `src/tx/` 为本计划锚定值;若实际 wallet 代码结构不同,实现时同步调整前缀与此测试。

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cowboy_contracts.py -q`
Expected: FAIL — `AttributeError: 'CowboyPack' object has no attribute 'contracts_hit'`

- [ ] **Step 3: Add Contract dataclass + CONTRACTS + contracts_hit**

In `src/marshal_pack_cowboy/pack.py`, add the import at top:

```python
from dataclasses import dataclass, field
```

After the existing `_ECON_INVARIANTS` list, add:

```python
@dataclass
class Contract:
    id: str
    repos: list[str]
    trigger_paths: dict[str, list[str]]   # repo -> 路径前缀列表
    verify_invariants: list[str]


CONTRACTS = [
    Contract(id="tx-encoding", repos=["wallet", "node"],
             trigger_paths={"wallet": ["src/tx/"],
                            "node": ["types/src/transaction"]},
             verify_invariants=["contract.tx_encoding_roundtrip"]),
    Contract(id="runner-types", repos=["runner", "node"],
             trigger_paths={"runner": ["crates/runner-common/src/types"],
                            "node": ["runner/src/types"]},
             verify_invariants=["contract.runner_types_serde"]),
]

_CONTRACT_BY_ID = {c.id: c for c in CONTRACTS}
```

Add this method inside class `CowboyPack`:

```python
    def contracts_hit(self, scope: dict) -> list[str]:
        repo = scope.get("repo", "")
        paths = scope.get("diff_paths", [])
        hit = []
        for c in CONTRACTS:
            prefixes = tuple(c.trigger_paths.get(repo, []))
            if prefixes and any(p.startswith(prefixes) for p in paths):
                hit.append(c.id)
        return hit
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cowboy_contracts.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/marshal_pack_cowboy/pack.py tests/test_cowboy_contracts.py
git commit -m "feat(cowboy-pack): add cross-repo contract topology + contracts_hit"
```

---

## Task 3: cowboy-pack 分级细化 + review_plan

`classify_detailed` 返回 `{tier, reasons, contracts_hit, review_dimensions}`;`classify` 退化取 `tier`(保持协议)。误判向上。

**Files:**
- Modify: `src/marshal_pack_cowboy/pack.py`
- Test: `tests/test_cowboy_classify.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cowboy_classify.py`:

```python
from marshal_pack_cowboy.pack import CowboyPack


def test_execution_path_is_high_with_reason():
    pack = CowboyPack()
    d = pack.classify_detailed({"repo": "node",
                                "diff_paths": ["execution/src/execution/engine.rs"]})
    assert d["tier"] == "high"
    assert any("execution" in r for r in d["reasons"])


def test_system_address_change_is_high():
    pack = CowboyPack()
    d = pack.classify_detailed({"repo": "node",
                                "diff_paths": ["execution/src/runner/registry.rs"],
                                "diff_text": "Address::from_low_u64(0x91)"})
    assert d["tier"] == "high"


def test_contract_hit_forces_high():
    pack = CowboyPack()
    d = pack.classify_detailed({"repo": "wallet",
                                "diff_paths": ["src/tx/encode.js"]})
    assert d["tier"] == "high"
    assert "tx-encoding" in d["contracts_hit"]
    assert any("cross_repo_contract" in r for r in d["reasons"])


def test_docs_only_is_low():
    pack = CowboyPack()
    d = pack.classify_detailed({"repo": "node", "diff_paths": ["README.md"]})
    assert d["tier"] == "low"


def test_rpc_handler_is_mid():
    pack = CowboyPack()
    d = pack.classify_detailed({"repo": "node", "diff_paths": ["rpc/src/handlers.rs"]})
    assert d["tier"] == "mid"


def test_classify_str_still_returns_tier():
    pack = CowboyPack()
    assert pack.classify({"repo": "node",
                          "diff_paths": ["execution/src/execution/engine.rs"]}) == "high"


def test_review_plan_scales_with_tier():
    pack = CowboyPack()
    assert len(pack.review_plan("high")) == 6
    assert len(pack.review_plan("mid")) == 3
    assert len(pack.review_plan("low")) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cowboy_classify.py -q`
Expected: FAIL — `AttributeError: 'CowboyPack' object has no attribute 'classify_detailed'`

- [ ] **Step 3: Add review dimensions, classify_detailed, review_plan; rewrite classify**

In `src/marshal_pack_cowboy/pack.py`, replace the existing `_HIGH_PREFIXES` tuple and the `classify` method.

Replace `_HIGH_PREFIXES` block with:

```python
_HIGH_PREFIXES = (
    "execution/src/execution/engine",
    "execution/src/execution/transaction",
    "execution/src/execution/system_instruction",
    "execution/src/execution/basefee",
    "execution/src/runner/",
    "storage/src/speculative",
    "storage/src/process_block",
    "chain/",
)
_HIGH_SUBSTR = ("crypto", "_root")
_LOW_SUFFIXES = (".md",)
_LOW_SUBSTR = ("/tests/", "test_", "/scripts/", "tests.rs")
_SYS_ADDR_TOKENS = ("0x06", "0x09", "0x91", "0x92", "0x93", "0x94", "0x95")

REVIEW_DIMENSIONS = [
    {"name": "correctness", "prompt": "找出这个改动会怎样产生错误结果或破坏现有行为。"},
    {"name": "spec", "prompt": "实现是否偏离它所引用 CIP 的真实意图?指出语义漂移。"},
    {"name": "cross-repo", "prompt": "这个改动是否破坏跨 repo 契约(编码/类型序列化字节兼容)?"},
    {"name": "security", "prompt": "默认怀疑:有无越权、未校验输入、可被滥用的路径?"},
    {"name": "econ", "prompt": "gas/费用/escrow 守恒是否被破坏?burn+tip==fee?escrow 非负?"},
    {"name": "determinism", "prompt": "PVM 确定性:有无非确定来源、绕过 int guard、黑名单 import?"},
]
```

Replace the `classify` method with:

```python
    def classify(self, scope: dict) -> str:
        return self.classify_detailed(scope)["tier"]

    def classify_detailed(self, scope: dict) -> dict:
        paths = scope.get("diff_paths", [])
        text = scope.get("diff_text", "")
        reasons = []

        contracts = self.contracts_hit(scope)
        for cid in contracts:
            reasons.append(f"cross_repo_contract:{cid}")

        if any(p.startswith(_HIGH_PREFIXES) for p in paths):
            reasons.append("high-risk path (execution/storage/chain consensus)")
        if any(s in p for p in paths for s in _HIGH_SUBSTR):
            reasons.append("crypto / *_root computation")
        if any(t in text for t in _SYS_ADDR_TOKENS):
            reasons.append("system actor address logic")
        if any(lbl in ("cip:new", "cip:interface-change")
               for lbl in scope.get("labels", [])):
            reasons.append("CIP new / interface change")

        if contracts or reasons:
            tier = "high"
        elif paths and all(p.endswith(_LOW_SUFFIXES) or any(s in p for s in _LOW_SUBSTR)
                           for p in paths):
            tier = "low"
        else:
            tier = "mid"
            reasons.append("default mid (ordinary actor / RPC handler)")

        return {"tier": tier, "reasons": reasons, "contracts_hit": contracts,
                "review_dimensions": [d["name"] for d in self.review_plan(tier)]}

    def review_plan(self, tier: str) -> list[dict]:
        n = {"high": 6, "mid": 3, "low": 1}.get(tier, 3)
        return REVIEW_DIMENSIONS[:n]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cowboy_classify.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Run full suite (existing cowboy-pack/classifier tests still green)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (37 passed)

> 若 `tests/test_cowboy_pack.py` 因分级前缀收窄而失败,检查其断言用的路径是否仍落在新 `_HIGH_PREFIXES` 内;按实际改动调整该测试的路径输入(不放宽产品逻辑)。

- [ ] **Step 6: Commit**

```bash
git add src/marshal_pack_cowboy/pack.py tests/test_cowboy_classify.py
git commit -m "feat(cowboy-pack): detailed classification (tier+reasons+review dims)"
```

---

## Task 4: cowboy-pack 跨 repo list_invariants

契约命中时,把契约的 verify_invariants 牵进适用清单(可能指向别的 repo)。

**Files:**
- Modify: `src/marshal_pack_cowboy/pack.py`
- Test: `tests/test_cowboy_contracts.py` (扩充)

- [ ] **Step 1: Add failing test**

Append to `tests/test_cowboy_contracts.py`:

```python
def test_wallet_change_surfaces_tx_contract_invariant():
    pack = CowboyPack()
    invs = pack.list_invariants({"repo": "wallet",
                                 "diff_paths": ["src/tx/encode.js"]})
    ids = [i.id for i in invs]
    assert "contract.tx_encoding_roundtrip" in ids
    # 该契约不变量本体住在 node
    inv = next(i for i in invs if i.id == "contract.tx_encoding_roundtrip")
    assert inv.location_repo == "node"


def test_node_econ_change_still_lists_econ_invariants():
    pack = CowboyPack()
    invs = pack.list_invariants({"repo": "node",
                                 "diff_paths": ["execution/src/execution/transaction.rs"]})
    assert "econ.fee_conservation" in [i.id for i in invs]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cowboy_contracts.py -q`
Expected: FAIL — `contract.tx_encoding_roundtrip` not in ids

- [ ] **Step 3: Add contract invariant defs + extend list_invariants**

In `src/marshal_pack_cowboy/pack.py`, after `_CONTRACT_BY_ID = {...}` add:

```python
_CONTRACT_INVARIANTS = {
    "contract.tx_encoding_roundtrip": InvariantDef(
        id="contract.tx_encoding_roundtrip", domain="cross-repo", spec_ref="CIP-?",
        executor_kind="conformance-vector", location_repo="node",
        location_path="types/src/transaction.rs", location_test="tx_encoding_golden_vectors",
        severity="high",
        run_command=["cargo", "test", "-p", "cowboy-types", "tx_encoding_golden_vectors",
                     "--", "--exact"]),
    "contract.runner_types_serde": InvariantDef(
        id="contract.runner_types_serde", domain="cross-repo", spec_ref="C-1",
        executor_kind="conformance-vector", location_repo="node",
        location_path="runner/src/types.rs", location_test="runner_types_serde_compat",
        severity="high",
        run_command=["cargo", "test", "-p", "cowboy-node-runner", "runner_types_serde_compat",
                     "--", "--exact"]),
}
```

Replace the existing `list_invariants` method with:

```python
    def list_invariants(self, scope: dict) -> list[InvariantDef]:
        out = []
        if scope.get("repo") == "node":
            out.extend(_ECON_INVARIANTS)
        seen = {i.id for i in out}
        for cid in self.contracts_hit(scope):
            for inv_id in _CONTRACT_BY_ID[cid].verify_invariants:
                inv = _CONTRACT_INVARIANTS.get(inv_id)
                if inv and inv.id not in seen:
                    out.append(inv)
                    seen.add(inv.id)
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cowboy_contracts.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (39 passed)

- [ ] **Step 6: Commit**

```bash
git add src/marshal_pack_cowboy/pack.py tests/test_cowboy_contracts.py
git commit -m "feat(cowboy-pack): surface cross-repo contract invariants in list_invariants"
```

---

## Task 5: CLI 骨架 + `_db_url` + `classify` 命令

CLI 入口、绝对路径 db 解析、第一个子命令。

**Files:**
- Create: `src/marshal_core/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
import json
import subprocess
import sys
import os


def _run(args, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    proc = subprocess.run([sys.executable, "-m", "marshal_core.cli", *args],
                          capture_output=True, text=True, env=e,
                          cwd=os.path.dirname(os.path.dirname(__file__)))
    return proc


def test_classify_returns_json_tier():
    proc = _run(["classify", "--repo", "node",
                 "--paths", "execution/src/execution/engine.rs"])
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["tier"] == "high"
    assert "review_dimensions" in out


def test_classify_docs_only_low():
    proc = _run(["classify", "--repo", "node", "--paths", "README.md"])
    out = json.loads(proc.stdout)
    assert out["tier"] == "low"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: FAIL — `No module named marshal_core.cli`

- [ ] **Step 3: Create the CLI with `_db_url` + `classify`**

Create `src/marshal_core/cli.py`:

```python
"""Marshal 薄 CLI — skill 的确定性执行器。JSON 出入,错误非零退出。

db 路径解析为绝对 $MARSHAL_HOME/marshal.db,与 cwd 无关。
"""
import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marshal_core.knowledge.models import Base
from marshal_core.knowledge.store import Store
from marshal_pack_cowboy.pack import CowboyPack

_PACK = CowboyPack()


def _marshal_home() -> Path:
    env = os.environ.get("MARSHAL_HOME")
    if env:
        return Path(env)
    # cli.py 在 <home>/src/marshal_core/cli.py
    return Path(__file__).resolve().parents[2]


def _db_url() -> str:
    if os.environ.get("MARSHAL_DB"):
        return os.environ["MARSHAL_DB"]
    return f"sqlite:///{_marshal_home() / 'marshal.db'}"


def _session():
    engine = create_engine(_db_url())
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _emit(obj) -> int:
    print(json.dumps(obj, ensure_ascii=False))
    return 0


def _fail(msg: str) -> int:
    print(json.dumps({"error": msg}, ensure_ascii=False))
    return 1


def cmd_classify(a) -> int:
    scope = {"repo": a.repo, "diff_paths": a.paths, "diff_text": a.diff_text or "",
             "labels": a.labels or []}
    return _emit(_PACK.classify_detailed(scope))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="marshal")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("classify")
    c.add_argument("--repo", required=True)
    c.add_argument("--paths", nargs="*", default=[])
    c.add_argument("--diff-text", dest="diff_text", default="")
    c.add_argument("--labels", nargs="*", default=[])
    c.set_defaults(func=cmd_classify)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:  # 边界:把任何确定性失败转成 degraded 信号给 skill
        return _fail(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/marshal_core/cli.py tests/test_cli.py
git commit -m "feat(cli): marshal CLI skeleton with classify + absolute db path"
```

---

## Task 6: CLI `invariants` 命令

**Files:**
- Modify: `src/marshal_core/cli.py`
- Test: `tests/test_cli.py` (扩充)

- [ ] **Step 1: Add failing test**

Append to `tests/test_cli.py`:

```python
def test_invariants_lists_run_commands():
    proc = _run(["invariants", "--repo", "node",
                 "--paths", "execution/src/execution/transaction.rs"])
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    ids = [i["id"] for i in out]
    assert "econ.fee_conservation" in ids
    assert all("run_command" in i for i in out)


def test_invariants_cross_repo_contract():
    proc = _run(["invariants", "--repo", "wallet", "--paths", "src/tx/encode.js"])
    out = json.loads(proc.stdout)
    assert "contract.tx_encoding_roundtrip" in [i["id"] for i in out]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: FAIL — `invalid choice: 'invariants'`

- [ ] **Step 3: Add the invariants command**

In `src/marshal_core/cli.py`, add this function before `build_parser`:

```python
def cmd_invariants(a) -> int:
    scope = {"repo": a.repo, "diff_paths": a.paths}
    invs = _PACK.list_invariants(scope)
    return _emit([
        {"id": i.id, "severity": i.severity, "executor_kind": i.executor_kind,
         "location_repo": i.location_repo, "location_path": i.location_path,
         "location_test": i.location_test, "run_command": i.run_command}
        for i in invs
    ])
```

In `build_parser`, before `return p`, add:

```python
    iv = sub.add_parser("invariants")
    iv.add_argument("--repo", required=True)
    iv.add_argument("--paths", nargs="*", default=[])
    iv.set_defaults(func=cmd_invariants)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/marshal_core/cli.py tests/test_cli.py
git commit -m "feat(cli): add invariants command (with cross-repo contract invariants)"
```

---

## Task 7: CLI `ratchet-open` + `ratchet-close`

棘轮闭环的命令层。`ratchet-close` 双层防御: 缺 spawned_check 时 store 抛错 → CLI 非零退出。

**Files:**
- Modify: `src/marshal_core/cli.py`
- Test: `tests/test_cli.py` (扩充)

- [ ] **Step 1: Add failing test**

Append to `tests/test_cli.py` (用临时 db 隔离):

```python
def test_ratchet_open_then_close(tmp_path):
    db = {"MARSHAL_DB": f"sqlite:///{tmp_path/'t.db'}"}
    op = _run(["ratchet-open", "--desc", "bare 2**10000 逃逸",
               "--root-cause", "determinism-gap", "--escape-id", "esc-t1"], env=db)
    assert op.returncode == 0, op.stderr
    assert json.loads(op.stdout)["escape_id"] == "esc-t1"

    inv = json.dumps({
        "id": "det.bare_pow_literal", "domain": "determinism", "spec_ref": "M-B",
        "executor_kind": "proptest", "location_repo": "node",
        "location_path": "execution/src/pvm_executor.rs",
        "location_test": "prop_bare_pow_literal_blocked", "severity": "high"})
    cl = _run(["ratchet-close", "--escape-id", "esc-t1",
               "--spawned-check", "det.bare_pow_literal", "--inv-json", inv], env=db)
    assert cl.returncode == 0, cl.stderr
    assert json.loads(cl.stdout)["ok"] is True


def test_ratchet_close_without_spawned_check_fails(tmp_path):
    db = {"MARSHAL_DB": f"sqlite:///{tmp_path/'t2.db'}"}
    _run(["ratchet-open", "--desc", "d", "--root-cause", "c",
          "--escape-id", "esc-t2"], env=db)
    cl = _run(["ratchet-close", "--escape-id", "esc-t2",
               "--spawned-check", "", "--inv-json", "{}"], env=db)
    assert cl.returncode == 1
    assert "error" in json.loads(cl.stdout)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: FAIL — `invalid choice: 'ratchet-open'`

- [ ] **Step 3: Add the ratchet commands**

In `src/marshal_core/cli.py`, add before `build_parser`:

```python
def cmd_ratchet_open(a) -> int:
    s = _session()
    try:
        esc = Store(s).open_escape(
            id=a.escape_id, description=a.desc, root_cause_class=a.root_cause,
            change_ref=a.change_ref)
        return _emit({"escape_id": esc.id})
    finally:
        s.close()


def cmd_ratchet_close(a) -> int:
    if not a.spawned_check:
        return _fail("spawned_check is required to close an escape (棘轮纪律)")
    inv = json.loads(a.inv_json)
    inv.setdefault("domain_pack", "cowboy")  # InvariantRegistry.domain_pack 非空
    s = _session()
    try:
        store = Store(s)
        store.register_invariant(**inv, origin="ratchet", escape_id=a.escape_id)
        store.close_escape(a.escape_id, spawned_check=a.spawned_check)
        return _emit({"ok": True, "escape_id": a.escape_id,
                      "spawned_check": a.spawned_check})
    finally:
        s.close()
```

In `build_parser`, before `return p`:

```python
    ro = sub.add_parser("ratchet-open")
    ro.add_argument("--escape-id", dest="escape_id", required=True)
    ro.add_argument("--desc", required=True)
    ro.add_argument("--root-cause", dest="root_cause", default="")
    ro.add_argument("--change-ref", dest="change_ref", default=None)
    ro.set_defaults(func=cmd_ratchet_open)

    rc = sub.add_parser("ratchet-close")
    rc.add_argument("--escape-id", dest="escape_id", required=True)
    rc.add_argument("--spawned-check", dest="spawned_check", default="")
    rc.add_argument("--inv-json", dest="inv_json", required=True)
    rc.set_defaults(func=cmd_ratchet_close)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/marshal_core/cli.py tests/test_cli.py
git commit -m "feat(cli): add ratchet-open/ratchet-close with spawned_check guard"
```

---

## Task 8: CLI `gate-record` 命令

把一次门禁结果落 `GateRun` + `AuditLog`。

**Files:**
- Modify: `src/marshal_core/cli.py`
- Test: `tests/test_cli.py` (扩充)

- [ ] **Step 1: Add failing test**

Append to `tests/test_cli.py`:

```python
def test_gate_record_persists_run(tmp_path):
    db = {"MARSHAL_DB": f"sqlite:///{tmp_path/'g.db'}"}
    ev = json.dumps([{"name": "invariants", "outcome": "pass", "evidence_ref": "inv-x"}])
    proc = _run(["gate-record", "--change-ref", "abc123", "--verdict", "pass",
                 "--evidence-json", ev], env=db)
    assert proc.returncode == 0, proc.stderr
    assert isinstance(json.loads(proc.stdout)["run_id"], int)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: FAIL — `invalid choice: 'gate-record'`

- [ ] **Step 3: Add the gate-record command**

In `src/marshal_core/cli.py`, add before `build_parser`:

```python
def cmd_gate_record(a) -> int:
    gates = json.loads(a.evidence_json)
    s = _session()
    try:
        store = Store(s)
        run = store.record_gate_run(change_ref=a.change_ref, job_id=a.change_ref,
                                    verdict=a.verdict, evidence={"gates": gates})
        store.audit(event="gate_decision", actor="marshal-skill",
                    decision=a.verdict, refs={"change_ref": a.change_ref})
        return _emit({"run_id": run.id})
    finally:
        s.close()
```

In `build_parser`, before `return p`:

```python
    gr = sub.add_parser("gate-record")
    gr.add_argument("--change-ref", dest="change_ref", required=True)
    gr.add_argument("--verdict", required=True,
                    choices=["pass", "block", "needs_human"])
    gr.add_argument("--evidence-json", dest="evidence_json", default="[]")
    gr.set_defaults(func=cmd_gate_record)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/marshal_core/cli.py tests/test_cli.py
git commit -m "feat(cli): add gate-record command"
```

---

## Task 9: CLI `setup` 命令

建 `~/.claude/skills/marshal` 软链 + 校验 marshal 可 import。

**Files:**
- Modify: `src/marshal_core/cli.py`
- Test: `tests/test_cli.py` (扩充)

- [ ] **Step 1: Add failing test**

Append to `tests/test_cli.py`:

```python
def test_setup_creates_symlink(tmp_path):
    home = tmp_path / "fakehome"
    home.mkdir()
    proc = _run(["setup"], env={"HOME": str(home)})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    link = home / ".claude" / "skills" / "marshal"
    assert link.is_symlink()
    assert out["ok"] is True
    assert out["import_ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: FAIL — `invalid choice: 'setup'`

- [ ] **Step 3: Add the setup command**

In `src/marshal_core/cli.py`, add before `build_parser`:

```python
def cmd_setup(a) -> int:
    home = _marshal_home()
    skill_src = home / ".claude" / "skills" / "marshal"
    link_dir = Path(os.path.expanduser("~")) / ".claude" / "skills"
    link_dir.mkdir(parents=True, exist_ok=True)
    link = link_dir / "marshal"
    if link.is_symlink() or link.exists():
        if link.is_symlink():
            link.unlink()
        else:
            return _fail(f"{link} exists and is not a symlink; remove it manually")
    link.symlink_to(skill_src, target_is_directory=True)

    try:
        import marshal_pack_cowboy.pack  # noqa: F401
        import_ok = True
    except Exception:
        import_ok = False

    return _emit({"ok": True, "symlink": str(link), "target": str(skill_src),
                  "import_ok": import_ok,
                  "hint": None if import_ok else "run: pip install -e . in marshal venv"})
```

In `build_parser`, before `return p`:

```python
    st = sub.add_parser("setup")
    st.set_defaults(func=cmd_setup)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (47 passed)

- [ ] **Step 6: Commit**

```bash
git add src/marshal_core/cli.py tests/test_cli.py
git commit -m "feat(cli): add setup command (symlink skill + import check)"
```

---

## Task 10: Skill 本体(SKILL.md + references)

判断性编排逻辑。无单元测试,验证靠 Task 11 真实 dry-run。

**Files:**
- Create: `.claude/skills/marshal/SKILL.md`
- Create: `.claude/skills/marshal/references/gate-flow.md`
- Create: `.claude/skills/marshal/references/ratchet-flow.md`
- Create: `.claude/skills/marshal/references/review-orchestration.md`

- [ ] **Step 1: Write SKILL.md**

Create `.claude/skills/marshal/SKILL.md`:

```markdown
---
name: marshal
description: Use when reviewing a change before merge — runs the Marshal quality-gate cognitive loop (risk classification + invariant gate + adversarial review) on the current branch diff or a GitHub PR, and runs the escape→permanent-check ratchet. Triggers — "/marshal", "marshal gate", "跑一下 marshal", "marshal ratchet <bug>", "把这个漏过的 bug 上棘轮".
---

# Marshal Skill — 平台未建成前的本地质量大脑

你是 Marshal 的"大脑/编排器"(领域无关)。确定性工作外包给 marshal CLI;你只做判断性工作并汇总 `GateDecision`。

## 前置自检(每次先做)

用绝对路径调 CLI(不依赖 cwd 的 Python):

    MARSHAL_HOME=${MARSHAL_HOME:-/home/ubuntu/workspace/marshal}
    PY="$MARSHAL_HOME/.venv/bin/python"

跑一次 `"$PY" -m marshal_core.cli classify --repo node --paths README.md`。
若失败(no module / venv 缺失)→ 提示用户先在 marshal 仓库跑 `"$PY" -m marshal_core.cli setup` 并 `pip install -e .`,然后停止。

## 路由

- `/marshal`            → 流 A,diff = 当前分支 vs base
- `/marshal <PR#>`      → 流 A,diff = `gh pr diff <PR#>`,change_ref = PR head SHA
- `/marshal ratchet "<bug>"` → 流 C

## 流 A — 门禁评估

详见 `references/gate-flow.md`。步骤摘要:
1. 取 diff,用 `git rev-parse --show-toplevel` 判定所在 repo(可能多个)。
2. 对每个 repo 调 `cli classify` → tier/reasons/contracts_hit/review_dimensions。
3. 调 `cli invariants` → 在对应 repo 跑每条 `run_command`;契约不变量去其 `location_repo` 跑。
4. 按 `review_dimensions` 调 `/code-review ultra`(高危全视角)做对抗式 review,默认怀疑。
5. 汇总 `GateDecision`:任一不变量 fail→block;高危+确认高severity发现→needs_human;跑不起来/超预算→needs_human+degraded;否则 pass。
6. `cli gate-record` 落库;有 PR# 且用户要 → 贴 PR 评论;终端打印摘要。
7. 若在已合并代码上确认高severity发现 → 提议转流 C。

## 流 C — 逃逸棘轮

详见 `references/ratchet-flow.md`。步骤摘要:
1. `cli ratchet-open --escape-id <新id> --desc "<bug>" --root-cause <你的分类> [--change-ref <sha>]`。
2. 起草:根因分类 + 候选永久检查(指向某 repo 的 proptest 名 + 路径 + run_command)。
3. 把草稿摆给用户,等其确认根因 + 选定检查。
4. `cli ratchet-close --escape-id <id> --spawned-check <inv-id> --inv-json '<InvariantDef字段>'`。
   (spawned_check 为空 CLI 会拒绝 — 这是棘轮纪律,不要绕过。)
5. 提示去 `<repo>` 把这条 proptest 真正实现;可顺手起草测试骨架。

## 铁律

- **降级不谎报**:任何 CLI 错误(stdout 含 `"error"` 或非零退出)→ 对应门禁记 degraded,verdict 至少 needs_human,显式告诉用户哪步没跑成。绝不把"没审成"说成"审过了"。
- **高危发现终审归人**:你只产出"发现+severity+confidence",高危一律 needs_human。
- **不硬阻断**:你给 verdict 和评论,挡不住 merge — 如实说明这是建议态。
```

- [ ] **Step 2: Write references/gate-flow.md**

Create `.claude/skills/marshal/references/gate-flow.md`:

```markdown
# 流 A — 门禁评估细节

## 取 diff
- 无参:`base=$(git merge-base HEAD origin/main 2>/dev/null || git merge-base HEAD origin/devnet)`;`git diff --name-only $base...HEAD` 取改动路径;`git diff $base...HEAD` 取 diff_text。
- `<PR#>`:`gh pr diff <PR#> --name-only` 取路径;`gh pr view <PR#> --json headRefOid -q .headRefOid` 取 change_ref。
- 多 repo:diff 可能跨多个 git 顶层目录;按 repo 分组,各自走分级+不变量。

## 调 CLI
- `"$PY" -m marshal_core.cli classify --repo <r> --paths <p1> <p2> --diff-text "<截断的diff>" --labels <l1>`
- `"$PY" -m marshal_core.cli invariants --repo <r> --paths <p1> <p2>`

## 跑不变量
对 `invariants` 返回的每条:`cd <workspace>/<inv.location_repo>` 然后跑 `inv.run_command`。
- 测试不存在(契约不变量本体可能未实现)→ 该不变量记 degraded,提示"契约缺验证,建议用 /marshal ratchet 补",**不当作 pass**。
- 跑失败 → 该门禁 outcome=fail。

## 汇总 GateDecision(verdict 优先级 block > needs_human > pass)
- 任一 active 不变量 fail → block
- 高危 tier + 确认的高 severity review 发现 → needs_human
- 任一步骤 degraded(CLI 错/测试缺/review 超预算)→ 至少 needs_human + 标 degraded
- 否则 → pass

## 落库与回写
- `"$PY" -m marshal_core.cli gate-record --change-ref <ref> --verdict <v> --evidence-json '<gates JSON>'`
- 有 PR# 且用户要:把发现贴成 PR 评论(可借 `/code-review ultra` 的 --comment)。
```

- [ ] **Step 3: Write references/ratchet-flow.md**

Create `.claude/skills/marshal/references/ratchet-flow.md`:

```markdown
# 流 C — 逃逸棘轮细节

棘轮是唯一"越用越紧"的复利机制。每个真漏过 → 至少一条永久检查进注册表。

## 入口
- 手动:`/marshal ratchet "<bug 描述>"`
- 自动晋升:流 A 在**已合并代码**上确认高 severity 发现 → 问用户是否开逃逸。

## 步骤
1. 选 escape_id(如 `esc-0007`;可先 `cli` 无对应 list 命令时用日期+序号约定)。
   `"$PY" -m marshal_core.cli ratchet-open --escape-id <id> --desc "<bug>" --root-cause <class> [--change-ref <sha>]`
2. 起草候选永久检查 — 必须是可落地的断言,不是文档:
   - 它该是哪个 repo 的哪条 proptest/conformance-vector?
   - location_path / location_test / run_command 各是什么?
   - InvariantDef 字段:id, domain, spec_ref, executor_kind, location_repo, location_path, location_test, severity。
3. 把根因分类 + 候选检查摆给用户,**等确认**。
4. `"$PY" -m marshal_core.cli ratchet-close --escape-id <id> --spawned-check <inv-id> --inv-json '<上面 InvariantDef 的 JSON>'`
   - 缺 spawned_check 会被 CLI 拒绝 — 这是纪律,不要绕。
5. 去 `<location_repo>` 起草这条 proptest 的测试骨架(让用户/后续把它写实)。

## 根因分类参考(root_cause_class)
determinism-gap / econ-conservation / cross-repo-contract / state-consensus / auth / input-validation。
```

- [ ] **Step 4: Write references/review-orchestration.md**

Create `.claude/skills/marshal/references/review-orchestration.md`:

```markdown
# ③ 对抗式 review 编排

## 视角数由 tier 决定(classify 已给 review_dimensions)
- high: correctness, spec, cross-repo, security, econ, determinism(全 6)
- mid: correctness, spec, cross-repo(前 3)
- low: correctness(1)

## 原则
1. **对抗式而非背书式**:prompt 是"找出这个改动会怎样出错/违反哪条 CIP 不变量",默认怀疑。
2. **防相关性盲区**:视角互异;靠分歧和 quorum 标问题。高危发现即便 review 全绿也保留 needs_human。
3. **结构化输出**:每条发现带 {dimension, severity(low/mid/high), confidence, location}。

## 执行
- 优先复用 `/code-review ultra`(云端多 agent)做高危层 review。
- 若 `/code-review ultra` 不可用或超预算 → 降级为本地少视角 review,并**显式标 degraded**。
- 把确认的高 severity 发现汇入流 A 的 GateDecision;如发生在已合并代码,提议转流 C。
```

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/marshal/
git commit -m "feat(skill): add marshal skill (SKILL.md + gate/ratchet/review references)"
```

---

## Task 11: 端到端冒烟 + 注册 skill(人工验证)

确认 CLI 在任意 cwd 可调、知识核全局唯一、skill 被发现。

- [ ] **Step 1: 跑 setup 建软链**

Run: `cd /home/ubuntu/workspace/marshal && .venv/bin/python -m marshal_core.cli setup`
Expected: JSON `{"ok": true, "symlink": ".../.claude/skills/marshal", "import_ok": true, ...}`

- [ ] **Step 2: 从另一个 repo 调 CLI(验证 cwd 无关 + db 绝对路径)**

Run:
```bash
cd /home/ubuntu/workspace/node && \
/home/ubuntu/workspace/marshal/.venv/bin/python -m marshal_core.cli classify \
  --repo node --paths execution/src/execution/transaction.rs
```
Expected: JSON `{"tier": "high", ...}`(在 node 目录下跑,仍用 marshal 的 pack;无报错)

- [ ] **Step 3: 跨 repo 契约冒烟**

Run:
```bash
/home/ubuntu/workspace/marshal/.venv/bin/python -m marshal_core.cli invariants \
  --repo wallet --paths src/tx/encode.js
```
Expected: JSON 数组含 `contract.tx_encoding_roundtrip`,其 `location_repo` 为 `node`。

- [ ] **Step 4: 棘轮闭环冒烟(写真实 marshal.db)**

Run:
```bash
H=/home/ubuntu/workspace/marshal; PY=$H/.venv/bin/python
$PY -m marshal_core.cli ratchet-open --escape-id smoke-1 \
  --desc "bare 2**10000 绕过 INT_GUARD" --root-cause determinism-gap
$PY -m marshal_core.cli ratchet-close --escape-id smoke-1 \
  --spawned-check det.bare_pow_literal \
  --inv-json '{"id":"det.bare_pow_literal","domain":"determinism","spec_ref":"M-B","executor_kind":"proptest","location_repo":"node","location_path":"execution/src/pvm_executor.rs","location_test":"prop_bare_pow_literal_blocked","severity":"high"}'
```
Expected: 第一条 `{"escape_id": "smoke-1"}`;第二条 `{"ok": true, ...}`。
清理: `git -C $H checkout marshal.db 2>/dev/null || true`(若 marshal.db 入库)。

- [ ] **Step 5: 验证全套测试 + 在 Claude Code 里 `/marshal` 被发现**

Run: `cd /home/ubuntu/workspace/marshal && .venv/bin/python -m pytest -q`
Expected: PASS (47 passed)
然后在任意 repo 的 Claude Code 会话输入 `/marshal`(无参),确认 skill 被加载、走完流 A 前置自检。

- [ ] **Step 6: 最终提交(若 Step 4 改了 marshal.db,确认其是否应入库)**

```bash
cd /home/ubuntu/workspace/marshal
git status
# 若 marshal.db 已被 .gitignore 忽略则无需处理;否则决定是否提交冒烟数据(通常不提交)
```

---

## 自检结论(spec 覆盖)

- 流 A(①②③汇总)→ Task 3/4/5/6/8 + Task 10 SKILL.md/gate-flow ✅
- 流 C 棘轮闭环 → Task 1/7 + ratchet-flow ✅
- B 跨 repo 契约 → Task 2/4/6 + Task 11 Step 3 ✅
- SQLite 知识核 + EscapeRegistry → Task 1 ✅
- cowboy-pack 三块增量(分级/契约/视角)→ Task 2/3/4 ✅
- 薄 CLI(6 子命令)→ Task 5-9 ✅
- 可调用包解析(软链 + 绝对 venv/db)→ Task 5(_db_url)/9(setup)/11 ✅
- 降级不谎报 → SKILL.md 铁律 + CLI `_fail` 非零退出 ✅
- 非目标(⑤⑥⑦/硬阻断/PostgreSQL)未被任何 Task 引入 ✅
```
