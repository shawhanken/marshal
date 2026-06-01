# Marshal Skill 设计 (平台未建成前的本地大脑桥梁)

> **定位:** 在 Marshal 平台(常驻服务 + GitHub App + PostgreSQL 硬门禁)建成前,用一个 Claude Code skill 提前落地方法论的**认知闭环**——作用在当前分支/PR diff 上,文件态(复用 marshal SQLite 知识核)、按需触发、建议态(不硬阻断)。覆盖三支柱里的 ① 分级 + ③ 对抗式 review + ④ 逃逸棘轮,跨 repo 契约感知。
>
> **源文档:** [`docs/methodology/ai-velocity-quality-methodology.zh.md`](../../methodology/ai-velocity-quality-methodology.zh.md) · [`docs/architecture/platform-architecture-design.zh.md`](../../architecture/platform-architecture-design.zh.md)
>
> **状态:** 设计经交互评审通过,转 writing-plans。
>
> **日期:** 2026-06-01

---

## 0. 目标与非目标

**目标:** skill 是 Marshal 平台 §10 演进路线第 1 步(②InvariantGate + ④Ratchet)+ 第 2/3 步(①Classifier + ③ReviewOrch)的**预演载体**。在平台的常驻/执法层就绪前,先把最高复利的认知闭环跑通,且其产物(知识核 + cowboy-pack)正是未来平台直接复用的种子。

**核心定位:** Marshal 是常驻服务平台;skill 不能复刻其"平台/执法层"(webhook 自动触发、merge queue、硬阻断、多团队隔离、PostgreSQL),但能复刻其真正高杠杆的**方法论认知闭环**。skill = 平台未上线时的"本地大脑/agent worker"。

**明确非目标(留给平台):**
- ⑤ ConformanceGov 分层规格治理、⑥ RuntimeWatch、⑦ Metrics dashboard
- Webhook 自动触发 / merge queue / required-status **硬阻断**(skill 只给 verdict + 评论,挡不住 merge)
- 多团队组织级隔离、PostgreSQL(用现成 SQLite)
- 不替代各 repo CI;不新写 proptest 不变量本体(棘轮**指向**测试,实现由人/后续完成;skill 可起草骨架)

---

## 1. 评审已定的关键决策

| # | 决策点 | 选择 |
|---|---|---|
| Q1 | 触发面与作用对象 | **C**: 无参=当前分支 vs base 的 diff;`<PR#>`=拉远端 PR。对齐 `/code-review ultra` |
| Q2 | repo 作用域 | **B**: 跨 repo 感知——diff 命中已知契约文件时,主动去关联 repo 跑对应 conformance 不变量 |
| Q3 | 领域知识来源 | **A**: skill 复用并扩展 `marshal_pack_cowboy` 作为领域真相源;skill 本身领域无关 |
| Q4 | 棘轮喂入 | **C**: 手动上报(`/marshal ratchet`)+ review 发现自动晋升,两者共用开条目逻辑 |
| 形态 | 落地形态 | **方案 1**: skill 编排(判断性)+ marshal 薄 CLI(确定性) |
| 知识核 | 持久真相源 | 复用现有 SQLite `marshal.db` + Store;**新增 `EscapeRegistry` 表** |
| 位置 | skill 文件位置 | **(a)**: 住 `marshal/.claude/skills/`,软链到 `~/.claude/skills/` 解决任意 repo 可调用 |

---

## 2. 整体架构

```
用户:  /marshal                          (无参 → 当前分支 vs base 的 diff)
       /marshal <PR#>                     (拉远端 PR diff)
       /marshal ratchet "<漏网bug描述>"    (棘轮:手动开逃逸)

┌─────────────────────────────────────────────────────────┐
│  SKILL「marshal」 (领域无关大脑/编排器)                       │
│   判断性工作:                                              │
│    · 解析 diff、决定走哪条流程(gate / ratchet)               │
│    · 编排 ③ /code-review ultra 多视角对抗 review            │
│    · 起草棘轮的根因分类 + 候选永久检查                        │
│    · 汇总 GateDecision(pass/block/needs_human) 写回         │
└───────────────┬─────────────────────────────────────────┘
                │ Bash 调用 (确定性工作外包)
┌───────────────▼─────────────────────────────────────────┐
│  marshal CLI: $MARSHAL_HOME/.venv/bin/python -m marshal_core.cli │
│    classify / invariants / ratchet-open / ratchet-close /  │
│    gate-record / setup                                     │
│   ← 读领域真相: marshal_pack_cowboy (分级规则/契约拓扑/不变量/视角)│
│   ← 读写知识核: $MARSHAL_HOME/marshal.db (SQLite, 现有 Store) │
└─────────────────────────────────────────────────────────┘
```

**三条纪律(对齐 Marshal):**
1. **skill 只判断、不存状态** —— 持久真相在 `marshal.db`,领域知识在 `cowboy-pack`。skill 无状态、可随时重跑。
2. **确定性 vs 判断性硬分离** —— 能写成代码的(分级匹配、契约拓扑查找、spawned_check 约束)进 CLI 可单测;只有"AI 不可替代的判断"留 skill。
3. **降级不谎报** —— CLI 跑不起来 / review 超预算 → skill 显式回 `needs_human + degraded`,绝不把"没审成"伪装成"审过了"(方法论 §7.1)。

---

## 3. 端到端流程

### 流 A —— `/marshal` 或 `/marshal <PR#>`(门禁评估)

```
1. 取 diff
   · 无参 → git diff <base>...HEAD (base 自动探: origin/main 或 origin/devnet)
   · <PR#> → gh pr diff <PR#>,记录 change_ref = PR head SHA
   · 用 git rev-parse --show-toplevel 识别 diff 所在 repo(可能多个)

2. ① 分级 (CLI 确定性)
   cli classify --repo <r> --paths <files...>
   → {tier(high/mid/low), reasons[], contracts_hit[]}
   · 误判向上不向下

3. ② 不变量门禁 (CLI 选 + skill 跑, 建议态)
   cli invariants --repo <r> --paths <files...>  → 适用不变量清单(带 run_command)
   · skill 在对应 repo 跑 run_command (cargo test ...)
   · 跨 repo 契约命中 → 也 cd 进关联 repo 跑对应 conformance 不变量   ← B
   · 任一 active 不变量失败 → 该门禁 outcome=fail

4. ③ 对抗式 review (skill 判断, 按 tier 决定视角数)
   · 视角清单读自 cowboy-pack.REVIEW_DIMENSIONS
   · 调 /code-review ultra(高危全视角) 或精简(中低危)
   · 默认怀疑;防相关性盲区 → 高危发现保留 needs_human

5. 汇总 GateDecision
   · 任一 active 不变量 fail              → block
   · 高危 + confirmed 高severity 发现      → needs_human
   · 跑不起来 / 超预算                     → needs_human + degraded
   · 否则                                → pass

6. 落库 + 回写 (CLI)
   cli gate-record ...  → GateRun + AuditLog
   · 有 PR# 且用户要 → 结果贴 PR 评论(复用 /code-review --comment 形态)
   · 终端打印 GateDecision 摘要

7. ③ 若在「已合并代码」上确认高 severity 发现 → 提议转棘轮(流 C)   ← B 自动晋升
```

### 流 C —— `/marshal ratchet "<bug>"`(棘轮,复利引擎)

```
1. cli ratchet-open --desc ... [--change-ref ...]  → 建 EscapeRegistry [status=open]
2. skill 起草: root_cause_class + 候选永久检查(指向某 repo 的 proptest 名 + 路径)
3. 🧑 人确认根因 + 选定检查
4. cli ratchet-close --escape-id ... --spawned-check ... --inv-json ...
   · 把选定检查写入 InvariantRegistry(origin=ratchet, escape_id=...)
   · EscapeRegistry.spawned_check = 该不变量 id
   · 数据库级硬约束: spawned_check 为空 → 拒绝 close(棘轮纪律)
5. skill 提示去 <repo> 把这条 proptest 真正实现(可顺手起草测试骨架)
```

关键: **棘轮的"检查"是落进注册表的不变量条目(永久资产),不是事后文档**(方法论 §3)。

---

## 4. 数据模型

### 4.1 知识核新增 `EscapeRegistry` 表(`knowledge/models.py`)

```python
class EscapeRegistry(Base):
    __tablename__ = "escape_registry"
    id:               Mapped[str]  = primary_key           # esc-0001
    domain_pack:      Mapped[str]  = index, default "cowboy"
    discovered_at:    Mapped[datetime] = default _now
    introduced_at:    Mapped[str | None]                   # git blame 近似(可空)
    root_cause_class: Mapped[str]                          # skill 起草、人确认
    change_ref:       Mapped[str | None]                   # 流 C-B 晋升时填 PR/SHA
    description:      Mapped[str]
    postmortem_ref:   Mapped[str | None]                   # 可空
    spawned_check:    Mapped[str | None]                   # → InvariantRegistry.id
    status:           Mapped[str]  = default "open"        # open | closed
```

`Store` 新增:
- `open_escape(**kw) -> EscapeRegistry`
- `close_escape(escape_id, spawned_check)` —— **spawned_check 为空则 raise**(棘轮硬约束)
- `list_open_escapes() -> list[EscapeRegistry]`

> `InvariantRegistry` / `GateRun` / `AuditLog` 已存在,字段够用,不改。

### 4.2 `cowboy-pack` 增量(`marshal_pack_cowboy/pack.py`)

**(1) 分级规则细化** —— `classify()` 返回 `(tier, reasons[])`,编码方法论附录 B:
- 路径命中 `execution/{engine,transaction,system_instruction,basefee}` / `storage/{speculative,process_block}` / `chain/` 共识 / `crypto` / `*_root` → high
- 触碰系统地址逻辑 `0x06/0x09/0x91-95` → high
- 命中跨 repo 契约文件 → high + 标 `cross_repo_contract`
- CIP label = 新增/接口变更 → high
- 普通 actor / RPC handler → mid;仅 `*.md`/test/script → low

**(2) 跨 repo 契约拓扑(B 核心)** —— 声明式 `CONTRACTS`(下为结构示意;每个 `trigger_paths` 的确切路径在实现时据 `wallet`/`node`/`runner` 实际代码填):
```python
CONTRACTS = [
  Contract(id="tx-encoding", repos=["wallet", "node"],
           trigger_paths={"wallet": ["<tx 编码相关源文件>"],
                          "node": ["types/src/transaction"]},
           verify_invariants=["contract.tx_encoding_roundtrip"]),
  Contract(id="runner-types", repos=["runner", "node"],
           trigger_paths={"runner": ["crates/runner-common/src/types"],
                          "node": ["runner/src/types"]},
           verify_invariants=["contract.runner_types_serde"]),
]
```
diff 命中某 contract 的 trigger_paths → 分级标高危 + 门禁去**所有相关 repo** 跑 `verify_invariants`。
> 注: 这两条契约的 `verify_invariants` 不变量本体当前**可能尚不存在**(对齐方法论附录 A 的 golden vectors)。本切片只负责"命中→提示去验";不变量本体若缺,门禁记 degraded + 提示用棘轮补,不伪装成已验。

**(3) review 视角清单** —— `REVIEW_DIMENSIONS = ["correctness","spec","cross-repo","security","econ","determinism"]`,各配一句对抗式 prompt 提要。tier→视角数: high=全部, mid=前 3, low=1。

### 4.3 CLI 契约(`marshal_core/cli.py`,JSON in/out)

| 命令 | 入参 | 出(JSON) |
|---|---|---|
| `setup` | — | 建 `~/.claude/skills/marshal` 软链 + 校验 `.venv` 可 import marshal |
| `classify` | `--repo --paths` | `{tier, reasons[], contracts_hit[]}` |
| `invariants` | `--repo --paths` | `[{id, run_command, severity, location}]`(含 contracts_hit 牵出的跨 repo 不变量) |
| `ratchet-open` | `--desc --change-ref?` | `{escape_id}` |
| `ratchet-close` | `--escape-id --spawned-check --inv-json` | `{ok}` 或 spawned_check 空 → 非零退出 |
| `gate-record` | `--change-ref --verdict --evidence-json` | `{run_id}` |

每条命令出错都 JSON 带 `{"error": ...}` 且非零退出码,skill 据此走 degraded。

---

## 5. 可调用包解析((a) 的关键卡点)

**子问题 1 — skill 在任意 repo 都能被发现:** 真相源放 `marshal/.claude/skills/marshal/`,软链到用户级:
```
~/.claude/skills/marshal → /home/ubuntu/workspace/marshal/.claude/skills/marshal
```
单一真相、随 marshal 仓库演进、任意 repo 可见。由 `cli setup` 一次性建链。

**子问题 2 — CLI 任意 cwd 可调 + 知识核位置固定:** skill 用绝对路径的 marshal venv 调 CLI,不依赖 cwd 的 Python 环境:
```
$MARSHAL_HOME/.venv/bin/python -m marshal_core.cli ...
   MARSHAL_HOME 默认 /home/ubuntu/workspace/marshal (可被环境变量覆盖)
```
- `marshal.db` 由 CLI 解析成 **`$MARSHAL_HOME/marshal.db` 绝对路径**(不随 cwd 变)→ 知识核全局唯一。
- skill 用 `git rev-parse --show-toplevel` 判定 diff 所在 repo,其父目录定位 workspace root,从而 `cd` 进关联 repo 跑跨 repo 不变量。

**一次性 setup:** `cli setup` 建软链 + 校验 `pip install -e .` 已生效。SKILL.md 开头放自检: 链接/venv 缺失就提示先跑 setup。

---

## 6. 测试策略(平台吃自己的狗粮)

确定性逻辑全在 CLI/pack/store,**全部可单测**(pytest,marshal 已有 `tests/`):
- `classify`: 高危路径/系统地址/CIP-label/契约命中 → tier+reasons 断言;**误判向上**用例
- `invariants`: 单 repo 选集 + **契约命中牵出跨 repo 不变量**用例(B 回归)
- `EscapeRegistry`: `open→close` 正常路径;**`close` 缺 spawned_check 必须 raise**(棘轮硬约束核心回归)
- CLI: JSON 出入 round-trip + 错误路径非零退出

skill 的判断性逻辑(review 编排/根因起草)不写自动化测试,靠真实 diff 手验。

---

## 7. 文件清单

**marshal 仓库增量:**
```
src/marshal_core/cli.py                    新增 (薄 CLI + setup)
src/marshal_core/knowledge/models.py       +EscapeRegistry
src/marshal_core/knowledge/store.py        +open/close/list_escape
src/marshal_pack_cowboy/pack.py            +分级细化 +CONTRACTS +REVIEW_DIMENSIONS
tests/test_classify.py / test_ratchet.py / test_cli.py   新增
```

**skill 本体:**
```
.claude/skills/marshal/SKILL.md            主流程(流 A / 流 C 编排) + 开头自检
  references/gate-flow.md                  门禁评估细节
  references/ratchet-flow.md               棘轮细节
  references/review-orchestration.md       ③ 多视角 /code-review ultra 编排
```

---

## 8. 范围边界小结

**做:** 流 A 门禁评估(①②③汇总)、流 C 棘轮闭环、B 跨 repo 契约感知、SQLite 知识核 + EscapeRegistry、cowboy-pack 三块增量、薄 CLI、可调用包解析。

**不做:** ⑤/⑥/⑦、webhook/merge queue/硬阻断、多团队隔离、PostgreSQL、不写不变量本体、不替代各 repo CI。

---

## 修订记录
- **2026-06-01 v1** — 四段设计 + 可调用包解析经交互评审通过,整合成文。
