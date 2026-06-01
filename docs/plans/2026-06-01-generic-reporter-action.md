# 通用 Reporter Action 重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 walking-skeleton 遗留的「Marshal 逻辑下沉到 node」味道——把 node 里硬编码不变量映射的 `scripts/marshal_report.py` 重构为一个**项目无关的通用 reporter**,「跑哪些不变量、怎么跑」由 Marshal 大脑下发,node 对 Marshal 的耦合收缩到「一行 workflow 引用 + 它自己的 proptest」。

**Architecture:** **拉模型**——通用 reporter 在 target repo 的 CI 里先 `POST /plan`(repo+sha+diff_paths)向大脑要「本次改动适用的不变量 + 各自的运行命令(argv)」,照单执行后 `POST /results`。「怎么跑某条不变量」(如 `cargo test -p cowboy-execution ...`)属领域知识,放进 `cowboy-pack`,不进 node。reporter 只会「执行给定 argv + 收集 pass/fail + 回报」,对 cargo/rust/node 零知识。

**Tech Stack:** Python(marshal_core,既有 venv);GitHub composite Action(`action.yml`);node 侧仅保留 Rust proptest + 一行 action 引用。

**关键设计决策(D-R1):** 采用**拉模型**(reporter 问大脑「跑什么」)而非推模型(大脑用 workflow_dispatch 注入)。理由:拉模型下 target repo 的 CI 完全无状态、无 Marshal 业务知识,只需一行通用 action;不变量清单的真相单一地留在大脑/领域包。代价:CI 运行时多一次 `/plan` 往返(可接受)。

**前置状态:** walking-skeleton 已完成(marshal `feat/walking-skeleton-econ`:contracts/知识核/领域包/InvariantGate/Orchestrator/GitHub 适配器/18 测试;node `feat/marshal-econ-invariants`:3 条 proptest + 旧 `marshal_report.py` + workflow)。本 plan 在这两分支基础上继续(或新开 `feat/generic-reporter`,由执行者定;以下命令默认在现有分支续做)。

**设计参考:** `docs/architecture/platform-architecture-design.zh.md` §2(执行器)、§3.2(DispatchJob/StructuredResult)、§4(核心/领域包)、§8(薄 reporter)。

---

## File Structure

**marshal/(本仓库)**
```
src/marshal_core/domain_pack.py        # 修改: InvariantDef 增 run_command 字段
src/marshal_pack_cowboy/pack.py        # 修改: 3 条不变量填 run_command (cargo argv)
src/marshal_core/modules/orchestrator.py  # 修改: 增 plan(event) -> PlanResponse
src/marshal_core/contracts.py          # 修改: 增 PlanResponse 契约
src/marshal_core/adapters/api.py       # 修改: 增 POST /plan 端点
src/marshal_core/executor/__init__.py  # 新建 (空)
src/marshal_core/executor/reporter.py  # 新建: 通用 reporter (拉/执行/回报)
action.yml                             # 新建: composite GitHub Action 包装 reporter
tests/test_planner.py                  # 新建
tests/test_reporter.py                 # 新建
```

**node/(独立 repo / 现有分支 feat/marshal-econ-invariants)**
```
scripts/marshal_report.py              # 删除 (B 类硬编码逻辑去除)
.github/workflows/marshal-econ.yml     # 重写: 用通用 action, 不再 hardcode 不变量
execution/src/econ_invariants.rs       # 保留不动 (A 类: node 自己的测试)
```

---

## Task 1: InvariantDef 增 run_command + cowboy-pack 填充

**Files:**
- Modify: `marshal/src/marshal_core/domain_pack.py`
- Modify: `marshal/src/marshal_pack_cowboy/pack.py`
- Test: `marshal/tests/test_cowboy_pack.py`(追加)

- [ ] **Step 1: 追加失败测试**(在 `tests/test_cowboy_pack.py` 末尾)

```python
def test_invariants_carry_run_command():
    from marshal_pack_cowboy.pack import CowboyPack
    invs = CowboyPack().list_invariants({"repo": "node", "diff_paths": []})
    by_id = {i.id: i for i in invs}
    cmd = by_id["econ.fee_conservation"].run_command
    # run_command 是可直接执行的 argv;领域知识(cargo/crate/test)在 pack 里
    assert cmd[:3] == ["cargo", "test", "-p"]
    assert "prop_fee_conservation" in cmd
```

- [ ] **Step 2: 跑确认失败**

Run: `cd marshal && ./.venv/bin/pytest tests/test_cowboy_pack.py::test_invariants_carry_run_command -v`
Expected: FAIL,`AttributeError: ... 'run_command'`。

- [ ] **Step 3: InvariantDef 增字段**

在 `src/marshal_core/domain_pack.py` 的 `InvariantDef` dataclass 增字段(放在 `severity` 之后):
```python
    severity: str = "mid"
    run_command: list[str] = field(default_factory=list)   # 可直接执行的 argv (由领域包提供)
```
并确保文件顶部 import 了 `field`:
```python
from dataclasses import dataclass, field
```

- [ ] **Step 4: cowboy-pack 填 run_command**

在 `src/marshal_pack_cowboy/pack.py` 的三条 `InvariantDef(...)` 各加 `run_command`:
```python
_ECON_INVARIANTS = [
    InvariantDef(id="econ.fee_conservation", domain="econ", spec_ref="CIP-3",
                 executor_kind="proptest", location_repo="node",
                 location_path="execution/src/econ_invariants.rs",
                 location_test="prop_fee_conservation", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-execution",
                              "prop_fee_conservation", "--", "--exact"]),
    InvariantDef(id="econ.settlement_sum_100", domain="econ", spec_ref="CIP-2",
                 executor_kind="proptest", location_repo="node",
                 location_path="execution/src/econ_invariants.rs",
                 location_test="prop_settlement_sum_100", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-execution",
                              "prop_settlement_sum_100", "--", "--exact"]),
    InvariantDef(id="econ.escrow_non_negative", domain="econ", spec_ref="CIP-2",
                 executor_kind="proptest", location_repo="node",
                 location_path="execution/src/econ_invariants.rs",
                 location_test="prop_escrow_non_negative", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-execution",
                              "prop_escrow_non_negative", "--", "--exact"]),
]
```

- [ ] **Step 5: 跑确认通过 + 全量**

Run: `cd marshal && ./.venv/bin/pytest tests/test_cowboy_pack.py -v`
Expected: 4 passed(原 3 + 新 1)。
Run: `./.venv/bin/pytest -q`
Expected: 19 passed(原 18 + 新 1)。

- [ ] **Step 6: Commit**

```bash
cd marshal && git add src/marshal_core/domain_pack.py src/marshal_pack_cowboy/pack.py tests/test_cowboy_pack.py
git commit -m "feat: InvariantDef.run_command; cowboy-pack supplies run argv"
```

---

## Task 2: Orchestrator.plan() + PlanResponse 契约

**Files:**
- Modify: `marshal/src/marshal_core/contracts.py`
- Modify: `marshal/src/marshal_core/modules/orchestrator.py`
- Test: `marshal/tests/test_planner.py`

- [ ] **Step 1: 写失败测试** `tests/test_planner.py`

```python
from marshal_core.contracts import NormalizedEvent
from marshal_core.knowledge.store import Store
from marshal_core.modules.orchestrator import Orchestrator
from marshal_pack_cowboy.pack import CowboyPack


def test_plan_returns_job_and_run_specs(db_session):
    orch = Orchestrator(pack=CowboyPack(), store=Store(db_session))
    ev = NormalizedEvent(kind="pr", repo="node", change_ref="abc123",
                         diff_paths=["execution/src/execution/transaction.rs"])
    plan = orch.plan(ev)
    assert plan.job_id == "inv-abc123"
    ids = {i["invariant_id"] for i in plan.invariants}
    assert "econ.fee_conservation" in ids
    # 每条都带可执行 argv
    fee = next(i for i in plan.invariants if i["invariant_id"] == "econ.fee_conservation")
    assert fee["run_command"][:2] == ["cargo", "test"]


def test_plan_empty_for_unknown_repo(db_session):
    orch = Orchestrator(pack=CowboyPack(), store=Store(db_session))
    ev = NormalizedEvent(kind="pr", repo="other", change_ref="z1")
    assert orch.plan(ev).invariants == []
```

- [ ] **Step 2: 跑确认失败**

Run: `cd marshal && ./.venv/bin/pytest tests/test_planner.py -v`
Expected: FAIL,`ImportError`/`AttributeError: ... 'plan'`。

- [ ] **Step 3: 增 PlanResponse 契约**

在 `src/marshal_core/contracts.py` 末尾追加:
```python
class PlanResponse(BaseModel):
    job_id: str
    invariants: list[dict] = []   # [{invariant_id, run_command}]
```

- [ ] **Step 4: Orchestrator.plan()**

在 `src/marshal_core/modules/orchestrator.py` 顶部 import 增 `PlanResponse`:
```python
from marshal_core.contracts import (
    NormalizedEvent, DispatchJob, StructuredResult, GateDecision, PlanResponse,
)
```
在 `Orchestrator` 类增方法:
```python
    def plan(self, event: NormalizedEvent) -> PlanResponse:
        """告诉 CI 执行器: 本次改动跑哪些不变量、各自怎么跑。复用 handle_event 的登记。"""
        job = self.handle_event(event)
        invs = self.pack.list_invariants({"repo": event.repo,
                                          "diff_paths": event.diff_paths})
        return PlanResponse(
            job_id=job.job_id,
            invariants=[{"invariant_id": i.id, "run_command": i.run_command}
                        for i in invs],
        )
```

- [ ] **Step 5: 跑确认通过 + 全量**

Run: `cd marshal && ./.venv/bin/pytest tests/test_planner.py -v`
Expected: 2 passed。
Run: `./.venv/bin/pytest -q`
Expected: 21 passed。

- [ ] **Step 6: Commit**

```bash
cd marshal && git add src/marshal_core/contracts.py src/marshal_core/modules/orchestrator.py tests/test_planner.py
git commit -m "feat: Orchestrator.plan() returns run-specs (job_id + invariant run_commands)"
```

---

## Task 3: POST /plan 端点

**Files:**
- Modify: `marshal/src/marshal_core/adapters/api.py`
- Test: `marshal/tests/test_planner.py`(追加端点测试)

- [ ] **Step 1: 追加失败测试**(`tests/test_planner.py` 末尾)

```python
def test_plan_endpoint(tmp_path, monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    monkeypatch.setenv("MARSHAL_DB", f"sqlite:///{tmp_path/'p.db'}")
    import marshal_core.adapters.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    r = client.post("/plan", json={"kind": "pr", "repo": "node",
                                   "change_ref": "abc123",
                                   "diff_paths": ["execution/src/x.rs"]})
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == "inv-abc123"
    assert any(i["invariant_id"] == "econ.fee_conservation" for i in body["invariants"])
```

- [ ] **Step 2: 跑确认失败**

Run: `cd marshal && ./.venv/bin/pytest tests/test_planner.py::test_plan_endpoint -v`
Expected: FAIL(404 / endpoint 不存在)。

- [ ] **Step 3: 增 /plan 端点**

在 `src/marshal_core/adapters/api.py` 增(import 处加 `NormalizedEvent` 已有;确保 `Orchestrator` 已 import):
```python
@app.post("/plan")
async def plan(event: NormalizedEvent):
    with _Session() as s:
        resp = Orchestrator(_PACK, Store(s)).plan(event)
    # 缓存事件供 /results 还原上下文 (与既有 _EVENTS 一致)
    _EVENTS[event.change_ref] = event
    return resp.model_dump()
```
(注:`NormalizedEvent` 作为请求体——FastAPI 会按其 pydantic schema 解析 JSON。)

- [ ] **Step 4: 跑确认通过 + 全量**

Run: `cd marshal && ./.venv/bin/pytest tests/test_planner.py -v`
Expected: 3 passed。
Run: `./.venv/bin/pytest -q`
Expected: 22 passed。

- [ ] **Step 5: Commit**

```bash
cd marshal && git add src/marshal_core/adapters/api.py tests/test_planner.py
git commit -m "feat: POST /plan endpoint (CI executor asks brain what to run)"
```

---

## Task 4: 通用 reporter(拉 /plan → 执行 argv → POST /results)

**Files:**
- Create: `marshal/src/marshal_core/executor/__init__.py`(空)
- Create: `marshal/src/marshal_core/executor/reporter.py`
- Test: `marshal/tests/test_reporter.py`

> reporter 必须对 cargo/rust/node 零知识——它只 (1) 问大脑要 run-specs、(2) 执行给定 argv、(3) 回报。可执行 argv 全部来自 /plan 响应。

- [ ] **Step 1: 写失败测试** `tests/test_reporter.py`

```python
from marshal_core.executor import reporter


class _FakeResp:
    def __init__(self, payload): self._p = payload
    def read(self): import json; return json.dumps(self._p).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_reporter_runs_planned_invariants(monkeypatch):
    posted = {}

    # 假大脑: /plan 返回两条带 argv 的不变量; /results 记录收到的 body
    def fake_urlopen(req, timeout=0):
        import json
        url = req.full_url
        if url.endswith("/plan"):
            return _FakeResp({"job_id": "inv-sha1", "invariants": [
                {"invariant_id": "a", "run_command": ["true"]},
                {"invariant_id": "b", "run_command": ["false"]},
            ]})
        if url.endswith("/results"):
            posted["body"] = json.loads(req.data.decode())
            return _FakeResp({"verdict": "block"})
        raise AssertionError(url)

    # 假执行: ["true"]→0(pass), ["false"]→1(fail)
    def fake_run(argv, capture_output=True, text=True):
        class R: pass
        r = R(); r.returncode = 0 if argv == ["true"] else 1
        return r

    monkeypatch.setattr(reporter.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(reporter.subprocess, "run", fake_run)

    rc = reporter.run(brain_url="http://brain", repo="node",
                      change_ref="sha1", diff_paths=["x"])
    assert rc == 0                                    # reporter 自身永远成功 (影子: 不阻断 CI)
    body = posted["body"]
    assert body["job_id"] == "inv-sha1"
    results = {r["invariant_id"]: r["passed"] for r in body["payload"]["results"]}
    assert results == {"a": True, "b": False}         # 忠实回报 pass/fail
    assert body["status"] == "ok"
```

- [ ] **Step 2: 跑确认失败**

Run: `cd marshal && ./.venv/bin/pytest tests/test_reporter.py -v`
Expected: FAIL,`ModuleNotFoundError: marshal_core.executor`。

- [ ] **Step 3: 实现 reporter**

`src/marshal_core/executor/__init__.py`: 空文件。

`src/marshal_core/executor/reporter.py`:
```python
"""通用 CI reporter — 项目无关。问大脑要 run-specs, 执行 argv, 回报结果。
对 cargo/rust/任何具体项目零知识: 可执行命令全部来自大脑 /plan 响应。"""
import json
import subprocess
import sys
import urllib.request


def _post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def run(brain_url: str, repo: str, change_ref: str, diff_paths: list[str]) -> int:
    brain_url = brain_url.rstrip("/")
    # 1) 问大脑: 本次改动跑哪些不变量、怎么跑
    plan = _post(f"{brain_url}/plan", {"kind": "pr", "repo": repo,
                                       "change_ref": change_ref,
                                       "diff_paths": diff_paths})
    job_id = plan["job_id"]
    results = []
    status = "ok"
    # 2) 照单执行 argv (对命令内容零知识)
    for inv in plan["invariants"]:
        argv = inv["run_command"]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True)
            passed = proc.returncode == 0
            detail = "" if passed else "command exited nonzero"
        except Exception as e:                         # 执行环境问题 → degraded, 不谎报
            passed, status, detail = False, "degraded", str(e)
        results.append({"invariant_id": inv["invariant_id"],
                        "passed": passed, "detail": detail})
    # 3) 回报
    resp = _post(f"{brain_url}/results", {
        "job_id": job_id, "schema_version": "1", "kind": "invariant",
        "payload": {"results": results}, "cost": 0.0, "status": status})
    print("marshal response:", json.dumps(resp))
    return 0                                            # 影子模式: reporter 自身永不让 CI 失败


def _main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--brain-url", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--change-ref", required=True)
    p.add_argument("--diff-paths", default="")          # 逗号分隔
    a = p.parse_args()
    paths = [x for x in a.diff_paths.split(",") if x]
    return run(a.brain_url, a.repo, a.change_ref, paths)


if __name__ == "__main__":
    sys.exit(_main())
```

- [ ] **Step 4: 跑确认通过 + 全量**

Run: `cd marshal && ./.venv/bin/pytest tests/test_reporter.py -v`
Expected: 1 passed。
Run: `./.venv/bin/pytest -q`
Expected: 23 passed。

- [ ] **Step 5: Commit**

```bash
cd marshal && git add src/marshal_core/executor/ tests/test_reporter.py
git commit -m "feat: generic project-agnostic CI reporter (pull plan, exec argv, report)"
```

---

## Task 5: composite GitHub Action 包装 reporter

**Files:**
- Create: `marshal/action.yml`

> 让任意 repo 用一行 `uses:` 接入。Action 用 Python 跑 `marshal_core.executor.reporter`。

- [ ] **Step 1: 写 action.yml**

`marshal/action.yml`:
```yaml
name: "Marshal Invariant Reporter"
description: "向 Marshal 大脑拉取适用不变量, 在本 repo CI 执行并回报 (影子安全, 不阻断)"
inputs:
  brain-url:
    description: "Marshal 大脑 URL"
    required: true
  repo:
    description: "本 repo 在 Marshal 领域包中的标识"
    required: true
  base-ref:
    description: "比较基线 (算 diff_paths), 默认 origin 默认分支"
    required: false
    default: ""
runs:
  using: "composite"
  steps:
    - name: Compute diff paths
      id: diff
      shell: bash
      run: |
        BASE="${{ inputs.base-ref }}"
        if [ -z "$BASE" ]; then BASE="$(git rev-parse HEAD~1 2>/dev/null || echo HEAD)"; fi
        PATHS=$(git diff --name-only "$BASE"...HEAD 2>/dev/null | paste -sd, -)
        echo "paths=$PATHS" >> "$GITHUB_OUTPUT"
    - name: Run Marshal reporter
      shell: bash
      run: |
        python3 "${{ github.action_path }}/src/marshal_core/executor/reporter.py" \
          --brain-url "${{ inputs.brain-url }}" \
          --repo "${{ inputs.repo }}" \
          --change-ref "${GITHUB_SHA}" \
          --diff-paths "${{ steps.diff.outputs.paths }}"
```
(注:reporter 仅用 Python 标准库,composite action 无需 pip 安装即可跑。)

- [ ] **Step 2: 本地冒烟验证 action 引用的脚本路径可独立运行**

起大脑:`cd marshal && ./.venv/bin/uvicorn marshal_core.adapters.api:app --port 8099 &` 然后 `sleep 3`。
跑 reporter CLI(用真实 econ argv 需 node 在场;此处只验证 CLI 参数解析 + /plan/results 往返,用一个不存在的命令会被记 fail,但 reporter 应仍 rc=0):
Run: `cd /home/ubuntu/workspace/node && python3 /home/ubuntu/workspace/marshal/src/marshal_core/executor/reporter.py --brain-url http://localhost:8099 --repo node --change-ref smoke1 --diff-paths execution/src/x.rs`
Expected: 打印 `marshal response: {...}`,进程 rc=0。
收尾:`pkill -f "uvicorn marshal_core"`。
(若 cargo 在场且 node 已编译,三条 econ invariant 应 pass→verdict pass;否则命令失败被忠实记 fail,均属正常——重点验证 reporter 流程与 rc=0。)

- [ ] **Step 3: Commit**

```bash
cd marshal && git add action.yml
git commit -m "feat: composite GitHub Action wrapping the generic reporter"
```

---

## Task 6: (node)删除 B 类硬编码,改用通用 action

**Files:**
- Delete: `node/scripts/marshal_report.py`
- Modify: `node/.github/workflows/marshal-econ.yml`
- 保留不动: `node/execution/src/econ_invariants.rs`(A 类)

> node 现有分支 `feat/marshal-econ-invariants`。本任务证明 node 对 Marshal 的耦合收缩到「一行 action 引用 + 它自己的 proptest」。

- [ ] **Step 1: 删除硬编码 reporter**

```bash
cd /home/ubuntu/workspace/node && git rm scripts/marshal_report.py
```

- [ ] **Step 2: 重写 workflow 用通用 action**

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
        with: { fetch-depth: 0 }      # 需要历史以算 diff
      - uses: dtolnay/rust-toolchain@stable
      # 接入 Marshal: 一行引用通用 action, 无任何不变量硬编码
      - uses: shawhanken/marshal@main
        with:
          brain-url: ${{ secrets.MARSHAL_BRAIN_URL }}
          repo: node
          base-ref: ${{ github.event.pull_request.base.sha }}
```
说明:node 不再含「跑哪些不变量/怎么跑」的任何知识——这些由大脑经 cowboy-pack 下发。node 只声明「我是 repo `node`、大脑在哪、用什么基线算 diff」。

- [ ] **Step 3: 验证 node 工作树 + proptest 未受影响**

Run: `cd /home/ubuntu/workspace/node && ls scripts/marshal_report.py 2>&1 || echo "deleted OK"`
Expected: `deleted OK`。
Run: `cargo test -p cowboy-execution econ_invariants 2>&1 | tail -3`
Expected: `3 passed`(A 类不变量原样有效)。

- [ ] **Step 4: Commit(node repo)**

```bash
cd /home/ubuntu/workspace/node
git add scripts/marshal_report.py .github/workflows/marshal-econ.yml
git commit -m "refactor: use generic marshal reporter action; drop bespoke report script"
```

---

## Task 7: 文档登记 + 收尾

**Files:**
- Modify: `marshal/docs/README.md`(plans 段)
- Modify: `marshal/docs/architecture/platform-architecture-design.zh.md`(可选:§8 注记 reporter 已通用化)

- [ ] **Step 1: docs 索引登记本 plan**

在 `marshal/docs/README.md` plans 相关处追加一行指向 `plans/2026-06-01-generic-reporter-action.md`,注明「把 target-repo 侧 reporter 通用化(拉模型 + action)」。

- [ ] **Step 2: 最终全量验证**

Run: `cd marshal && ./.venv/bin/pytest -q`
Expected: 23 passed。

- [ ] **Step 3: Commit**

```bash
cd marshal && git add docs/README.md docs/architecture/platform-architecture-design.zh.md
git commit -m "docs: register generic-reporter-action plan"
```

---

## 验收标准

- [ ] marshal `./.venv/bin/pytest -q` → 23 passed(新增 run_command/planner/plan-endpoint/reporter 测试)。
- [ ] node 侧 `scripts/marshal_report.py` 已删除;workflow 仅含一行 `uses: shawhanken/marshal@main` + 标准 checkout/toolchain,**零不变量硬编码**。
- [ ] node `cargo test -p cowboy-execution econ_invariants` → 3 passed(A 类不变量不受影响)。
- [ ] 本地冒烟:reporter CLI 走 `/plan`→执行→`/results` 往返,rc=0(影子不阻断)。
- [ ] grep 验证:`marshal_core/` 仍零 Cowboy 专属词(reporter 通用,不含 cargo/node);「怎么跑」的 argv 仅存在于 `marshal_pack_cowboy/`。

## 范围外

- 真实发布 `shawhanken/marshal@v1` tag/release(本 plan 用 `@main`;正式接入前应打 tag)。
- 真实 GitHub Checks API 写回(仍由后续 plan 处理)。
- 其余 walking-skeleton 遗留 nits(webhook 签名、/results 鉴权、_EVENTS 持久化)——各自后续 plan。
