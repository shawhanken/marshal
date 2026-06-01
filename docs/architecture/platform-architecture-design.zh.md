# Marshal 质量工程平台 · 总体架构设计(Design Spec)

> **定位:** **通用质量工程平台**的总体架构蓝图(broad blueprint)。平台核心**领域无关**(domain-agnostic),把任意项目的质量工程内容收拢为**领域包(Domain Pack)**;Cowboy 是**第一个领域包**,而非平台的绑定对象。覆盖《AI 高速研发下的质量工程方法论》7 个子系统如何协作、共享哪些数据契约、依赖与演进顺序、技术选型。每个子系统停在「架构级」,各自后续有独立的 spec→plan→实现周期。
>
> **源方法论:** [`docs/methodology/ai-velocity-quality-methodology.zh.md`](../methodology/ai-velocity-quality-methodology.zh.md)(本仓库)
>
> **项目名:** Marshal(西部法官——为质量执法、守住多 repo 边疆;technical 双关:*marshalling* = 跨语言序列化契约,正是平台内核之一)
>
> **状态:** v1.3 设计已评审通过(v1.1 通用化:核心 vs 领域包;v1.2 分层规格体系:宪法/修正案/代码 + 双权威轴;v1.3 领域知识的获取与生长)。转 writing-plans 出第一个切入点的实施计划中。
>
> **日期:** 2026-06-01

---

## 0. 背景与目标

多 repo、海量 AI 产出之下,人工逐行 review 在三个结构性不对称面前必然失败(产能 / 缺陷类型 / 跨边界,见源方法论 §1)。Marshal 的目标不是「让人审得更快」,而是用系统工程手段把验证从人迁移到机器、把人的注意力重新分配到不可替代的判断、并让系统从每次逃逸中越变越紧。

本平台落地方法论的三支柱(可执行不变量门禁 / 风险分级 / 逃逸棘轮)+ 两包裹(AI 对抗式 review / 运行时纵深防御)+ CIP 规格治理 + 度量体系,共 7 个子系统。

**关键定位(v1.1):这三支柱两包裹是领域无关的质量工程原语**——对任何代码项目都成立。绑定某个具体项目(如 Cowboy)的从来不是这些机制,而是塞进去的**内容**(哪些不变量、哪套规格体系、哪些路径规则)。因此 Marshal 从第一天就把「通用核心」与「领域内容」分离:核心保持纯净,Cowboy 作为第一个领域包接入。详见 §4。

---

## 1. 关键决策摘要(评审已定)

| # | 决策点 | 选择 | 含义 |
|---|---|---|---|
| D1 | 交付范围 | **总体架构蓝图(broad)** | 7 子系统架构级覆盖;不一次性下钻到可开工 spec |
| D2 | 现有资产 | **绿地优先** | audit-bot/cowpilot 仅作能力参考与可选数据源,不为迁就它们牺牲架构 |
| D3 | 控制平面 | **混合(独立中枢 + GitHub 适配器)** | 中枢做大脑+记忆;第一接入层只做 GitHub,其余 forge/CLI 留接口 |
| D4 | 覆盖边界 | **团队优先、组织就绪** | 数据模型按组织级抽象,第一期落地圈定本团队;他人领域只读消费 |
| D5 | 智能底座 | **Claude Agent SDK + provider 薄接口** | 通用 agent 基建外包给 SDK;provider 边界留薄接口为将来换模型留路 |
| D6 | 内部架构形态 | **模块化单体「大脑」+ 无状态执行器** | 有状态协调/记忆与无状态重计算分离 |
| D7 | 通用化策略 | **A 档:留缝 + 文档化路径** | 核心领域无关 + Cowboy 为内建第一领域包;**不**建插件运行时(待第二个领域包出现再做) |
| D8 | 分层规格模型 | **分层 spec_layers + 双权威轴;治理 (a) 档** | 规格非扁平:规范轴(宪法 < 修正案)与描述轴(代码为王)分离;静默抵触宪法 → 高 severity + needs_human,**不自动 block**(见 §4.5) |

---

## 2. 系统拓扑与组件

四层:**接入适配层 → 大脑(领域无关核心,模块化单体)→ 无状态执行器**,大脑独占一个**知识核**(持久状态);**领域包**横向为大脑各模块注入领域内容。

```
┌──────────────────────────────────────────────────────────────────┐
│  接入适配层 (Adapters)  —— 第一期只实现 GitHub                       │
│  GitHub App / Webhook ··· (预留: CLI / 其他 forge / IDE)            │
└───────────────────────────────┬──────────────────────────────────┘
                                 │ 规范化事件 (NormalizedEvent)
┌───────────────────────────────▼──────────────────────────────────┐
│  大脑 Brain · 领域无关核心 (模块化单体, 一个服务)                     │
│  ┌──────────────┐  事件路由 / 状态机 / 编排                          │
│  │ Orchestrator │  (PR / CIP / 棘轮 生命周期都是状态机)               │
│  └──────┬───────┘                                                  │
│  7 个领域无关模块 (边界清晰, 通过内部接口交互):                        │
│   ① Classifier   ② InvariantGate  ③ ReviewOrch  ④ Ratchet          │
│   ⑤ ConformanceGov   ⑥ RuntimeWatch   ⑦ Metrics                    │
│  ┌──────────────┐         ▲ 领域内容经接口注入                       │
│  │ Provider Seam │         │                                       │
│  └──────────────┘   ┌──────┴───────────────────────────────┐       │
└─────────────────────┤  领域包 Domain Pack (可插拔)            ├──────┘
        │ 派活          │  · 不变量定义集   · 分级规则            │ 读写
        │              │  · 分层规格体系   · review 维度+prompt  │
        │              │  · 运行时信号适配 · repo/契约拓扑       │
        │              │  ▸ 内建第一包: cowboy-pack             │
┌───────▼──────────┐   └────────────────────────────────────┘   ┌────▼────────────┐
│ 无状态执行器       │                                            │ 知识核 Knowledge │
│ A. CI 执行器       │                                            │ Core (单点记忆)  │
│ B. Agent Worker   │                                            │ 7 张表 (领域无关) │
└───────────────────┘                                            └─────────────────┘
```

**五条架构纪律:**

1. **大脑只协调、不重算。** 重计算外包给无状态执行器。大脑做三件事:管状态机、派活、把结构化结果落知识核并回写 GitHub。
2. **执行器无状态、可水平扩。** CI 执行器复用各 repo GitHub Actions runner;Agent Worker 按需起 SDK 进程。
3. **7 个模块互不直接读对方的表**,只通过内部接口交互。
4. **知识核是唯一持久真相源。**
5. **(v1.1)核心不含任何领域知识。** 7 个模块对「不变量/规格/路径规则」的具体内容一无所知,全部经领域包接口注入(§4)。Cowboy 知识只存在于 `cowboy-pack`。

---

## 3. 知识核数据契约(平台脊椎)

所有持久实体带 `domain_pack` / `team` / `repo` 维度字段——既支持 D4 多团队隔离,也支持 D7 多领域包共存。第一期 `domain_pack` 恒为 `cowboy`。

### 3.1 持久领域状态(7 张核心存储,schema 领域无关)

| 存储 | 服务子系统 | 关键字段 | 关联 |
|---|---|---|---|
| **InvariantRegistry** | ① 支柱1 | `id` · `domain_pack` · `domain` · `spec_ref` · `executor_kind`(proptest / conformance-vector / runtime-assert)· `location`(repo+path+test-name)· `severity`(含 **constitutional** 最高级)· `status` · `origin`(hand/ratchet)· `escape_id?` | ← 逃逸登记册;← conformance 矩阵 |
| **EscapeRegistry** | ③ 支柱3 | `id` · `discovered_at` · `introduced_at` · `time_to_detection` · `root_cause_class` · `postmortem_ref` · `spawned_check` · `status` | → 不变量注册表(必须产出≥1) |
| **ConformanceMatrix** | ⑤ 规格治理 | `spec_id` · `requirement_id` · `requirement_text` · **`source_layer`**(出自哪层:宪法/修正案/…)· **`source_ref`** · **`amends?`**(修订链:本要求覆盖了哪条旧要求)· **`effective`**(套用修正案后的有效要求视图)· `covered_by` or `waived` · `status` | → 不变量注册表;↔ 领域包指定的漂移看板 |
| **Classifications** | ② 支柱2 | `change_ref` · `tier`(high/mid/low)· `trigger_reasons[]`(含 **spec_layer_touched**)· `classifier_version` · `routed_owners[]` | → 门禁决策 |
| **Findings** | ④ 包裹A | `id` · `change_ref` · `dimension` · `lens` · `severity` · `confidence` · `quorum_verdict` · `loc` | → 门禁决策;可升级为逃逸 |
| **Metrics** | ⑦ 度量 | `metric` · `value` · `dims`(domain_pack/team/repo/tier)· `ts` | 聚合自其余表 |
| **AuditLog** | 全局 | append-only:`event` · `actor` · `decision` · `gate_outcome` · `refs` | 溯源、复盘、provenance |

注:表结构本身不含 Cowboy 语义;`domain`/`source_layer`/`dimension` 等取值由领域包定义,核心只当作不透明枚举存取。

### 3.2 流动契约(层间消息 schema,稳定接口)

```
NormalizedEvent   适配层 → 大脑    { kind: pr|cip|merge, repo, change_ref, diff_meta, labels, actor }
DispatchJob       大脑 → 执行器    { job_id, kind: invariant|review|impact, target, params, budget }
StructuredResult  执行器 → 大脑    { job_id, schema_version, payload(按 kind 强校验), cost, status }
GateDecision      大脑 → 适配层    { change_ref, tier, gates:[{name,outcome,evidence_ref}], verdict: pass|block|needs_human }
```

### 3.3 三条数据流闭环

1. **棘轮闭环**:`Finding`(确认为真漏过)→ 建 `EscapeRegistry` → **强制** `spawned_check` 落一条 `InvariantRegistry`(否则不能 close)。数据库级约束。
2. **conformance 闭环**:规格改动 → `ConformanceMatrix` 每行必须 `covered_by` 或 `waived` → 否则门禁 `block`。
3. **分级驱动闭环**:`NormalizedEvent` → `Classifications`(定 tier)→ 决定派哪些 `DispatchJob` → 汇总成 `GateDecision`。

### 3.4 关键取舍

不变量本身的「代码」不存知识核,只存**引用**(repo+path+test-name)。不变量是测试,真相在各 repo 代码里、跑在各 repo CI 里;知识核只做目录+状态+关联。

---

## 4. 通用化架构:核心 vs 领域包(v1.1)

> 策略 D7-A:**核心领域无关、领域内容收拢为可插拔的领域包、Cowboy 是内建第一包**。本期只划接口与边界(留缝),**不建动态插件运行时**——领域包以代码包形式编进发行物;待第二个真实领域包出现,再把动态发现/加载做实。

### 4.1 边界划分

| 归属 | 内容 | 说明 |
|---|---|---|
| **核心(Core,领域无关)** | Orchestrator + 状态机;7 个模块的**机制**;知识核 schema;4 个流动契约;执行器协议;门禁决策逻辑;失败策略;适配器框架;provider seam;**分层规格的解析/解析有效规格/漂移检测引擎** | 对「具体不变量/规格/路径」一无所知 |
| **领域包(Domain Pack)** | ①分级规则 ②不变量定义集 ③分层规格体系绑定 ④review 视角+prompt ⑤运行时信号适配 ⑥repo/契约拓扑 | 一切「这个项目特有的知识」 |

### 4.2 领域包契约(Core 调用的接口,概念级)

领域包 = **一份声明式 manifest + 一组类型化 hook**。核心通过这些接口取领域知识,绝不硬编码:

```
DomainPack {
  manifest: {
    id,                       # 如 "cowboy"
    repos[],                  # 纳管的 repo 列表
    spec_layers[],            # 分层规格体系 (见 §4.5): 每层 { id, role, authority, mutability, amends?, source }
    precedence_normative,     # 应然轴: 如 [whitepaper < cip]
    precedence_descriptive,   # 实然轴: 如 [whitepaper < cip < doc_amend < code]
    drift_board?,             # 漂移看板位置 (Cowboy: refs/wiki/drift.md)
    review_dimensions[],      # 该领域的 review 视角清单
  }
  # 类型化 hooks (核心在流程中回调):
  classify_rules() -> RuleSet                     # ① 路径/label/spec层 → tier 的规则(规则即数据)
  list_invariants(scope) -> [InvariantDef]        # ② 适用于本次改动的不变量定义(指向 repo 内测试)
  parse_spec_requirements(layer, diff) -> [Req]   # ⑤ 按层从规格 diff 抽取 requirement
  resolve_effective_spec() -> EffectiveSpec       # ⑤ 套用修正案 → 当前真正生效的规格视图
  review_prompts(dimension, tier) -> Prompt       # ③ 各视角的对抗式 prompt
  runtime_adapters() -> [SignalAdapter]           # ⑥ 运行时信号源接入
}
```

### 4.3 Cowboy 作为第一领域包(`cowboy-pack`)

把现有 Cowboy 专属内容全部归入 `cowboy-pack`,核心不再知道它们的存在:

- **分级规则**:附录B 的路径(`execution/`、`storage/speculative`)、系统地址 `0x06`/`0x09`/`0x91-95`、CIP-label、**触及的规格层** → 由 `classify_rules()` 提供。
- **不变量定义集**:gas 守恒、PVM 确定性、wallet↔node 字节契约、状态/共识(附录A)→ 由 `list_invariants()` 指向各 repo 内测试。
- **分层规格体系**:见 §4.5(whitepaper 宪法 / CIP 修正案 / doc-amendments 修正 / code 实然之锚);`drift_board = refs/wiki/drift.md`。
- **review 视角**:correctness/spec/cross-repo/security/econ/determinism + 各自 prompt。
- **运行时适配**:devnet 断言、CIP-2 dispute window/verification modes、WindTunnel 负载 → 各为一个 `SignalAdapter`。

### 4.4 A 档的明确边界(防过早抽象)

**本期做**:接口/边界(上述契约)、核心代码零 Cowboy 知识、`cowboy-pack` 编译进发行物。
**本期不做**(待第二个领域包触发):动态插件发现/加载、领域包 SDK/脚手架、领域包市场、跨领域包租户计费。

> 自检:契约面向「任意代码项目的质量工程」抽象,不偷偷把 Cowboy 概念塞进核心(核心代码不得出现 `CIP`/`gas`/`PVM` 字样,只有 `spec_layer`/`invariant`/`domain` 等中性词)。这条作为核心代码的一条 lint 级约束。

### 4.5 分层规格体系:宪法 / 修正案 / 代码(v1.2)

真实项目的规格不是扁平单层,而是**分层、带权威次序、带修订关系**的法律体系(Cowboy:白皮书=宪法、CIP=修正案、代码=实然真相)。核心提供分层处理引擎,领域包提供层定义。

**(1)两条权威轴——必须分开用,绝不混为一谈:**

| 轴 | 排序 | 回答 | 用在 |
|---|---|---|---|
| **规范轴(应然)** | 宪法(白皮书)为根基,**修正案(CIP)在其触及之处覆盖宪法** | 「改动是否被规格授权?」 | ⑤ ConformanceGov、① 分级 |
| **描述轴(实然)** | **代码 > 文档修正案 > CIP > 白皮书** | 「系统实际行为是什么?」 | ⑥ RuntimeWatch、漂移检测 |

二者不矛盾:修宪后新条款覆盖旧宪法文本(规范轴 修正案>宪法);但「系统实际如何」以代码为锚(描述轴 实然优先)。核心**绝不能只持一条权威序**,否则会把「CIP 合法覆盖白皮书」误判成「CIP 违反白皮书」。

**(2)有效规格(effective spec):** ⑤ 通过 `resolve_effective_spec()` 把宪法套用所有修正案,解析出**当前真正生效的要求**——这才是 ② 不变量门禁对齐的目标,而非任一单层原文。源自宪法根基的要求所派生的不变量,标 **constitutional 级、最高 severity、最不可豁免**。

**(3)Marshal 检测三类跨层漂移(单层系统看不见):**
1. **宪法↔修正案漂移(治理红线)**:某 CIP 实质抵触白皮书却未显式声明修订 → **治理 (a) 档:标高 severity + `needs_human`,不自动 block**(是合法演进还是违规越权,留人裁决;Marshal 只负责让它无法被忽略)。
2. **修正案↔修正案冲突**:两个 CIP 互相矛盾 → 同 (a) 档处理。
3. **规格↔代码漂移(实然 vs 应然)**:代码偏离有效规格 → 按描述轴代码为真,产出 drift 条目(补文档或判定代码 bug),联动 `drift_board`。

### 4.6 领域知识的获取与生长(v1.3)

> 领域知识不是静态配置,而是「**静态领域包 + 知识核累积态**」,且随运行不断生长。

**(1)某次评审临场组装的可用领域知识 = 三层叠加:**
```
= ① 静态领域包(版本控制、人审过的 manifest + hooks + 不变量定义)
+ ② 知识核累积态(该项目的逃逸登记册 / conformance 矩阵 / 调优过的 prompt)
+ ③ JIT 上下文(评审当下 agent 现读被改文件周边代码、相关规格条款)
```

**(2)冷启动——为新项目建立初始领域包(AI 起草 → 人审定):**
1. **约定探测(确定的自动拿)**:语言 → 默认测试框架(Rust→proptest);`CODEOWNERS` → owner 路由;CI 配置 → 现有门禁;目录结构 → 候选高危路径;共享序列化 → 候选契约不变量。
2. **AI 辅助发现(难的让 agent 草拟)**:Agent fan-out 读 `README`/`CLAUDE.md`/`docs`/规格目录、**现有测试套件**、issue/PR/post-mortem → 候选领域包草稿(候选不变量 / 分级规则 / 规格层 / review 视角)。高杠杆技巧:**把现有测试当种子不变量**直接登记。
3. **人工策展(审定)**:审 AI 草稿、改 manifest、删错补漏 → commit 成初始 `xxx-pack`。

> 关键认知:**初始包可以很薄。** 不需要冷启动就拥有完整领域知识——需要的是生长机制。薄包 + 棘轮,会让网在项目真正被咬处自动织密(契合方法论「靠棘轮收紧」)。

**(3)持续生长(领域知识是活资产,无需再手工大规模喂):**
- **棘轮(主引擎,支柱3)**:每个真漏过 → 一条新不变量进该项目注册表;项目越跑越精准,且精准在最痛处。
- **conformance 增长(⑤)**:规格演进 → 矩阵新增 requirement ↔ 测试映射。
- **prompt 调优(③)**:误报反馈 → 精修对应视角 prompt。

**(4)晋升回路(DB ⇄ 包):** 知识核累积的知识(尤其棘轮产出的不变量、调优过的 prompt)定期**收割回静态领域包**(版本控制、人审),避免领域知识只活在数据库、换环境即丢。这是「静态包 ⇄ 动态核」的双向流动。

**(5)领域包自身要过门禁:** 领域包草稿是 AI 产出、且定义别人的门禁 → 属高危改动,走人审签字;`fake-pack` 契约测试保证核心对任意薄/厚的包都能跑通。平台对领域知识的获取,吃自己的狗粮。

---

## 5. 7 个子系统映射(Cowboy 专属处标注为「领域包提供」)

每个子系统 = 大脑模块(机制,核心)+ 执行器 + 知识核表 + **领域包注入点**。

**① Classifier · 风险分级(支柱2)**
- 机制(核心):`diff_meta` + **触及的规格层** → 规则匹配 → tier → owner 路由;**误判向上不向下**。**动宪法(白皮书)= 最高 tier**(罕见、根基性、需最广签字);动修正案(CIP)= 高;动实现 = 看爆炸半径。
- 领域包提供:`classify_rules()`(Cowboy 的路径/地址/label/层规则)。

**② InvariantGate · 不变量门禁(支柱1)**
- 机制(核心):选适用不变量 → 下发 CI job → 任一 active 失败则 `block`;**constitutional 不变量最高 severity、最不可豁免**。
- 领域包提供:`list_invariants()`(Cowboy 的 gas/PVM/跨 repo 契约定义)。

**③ ReviewOrch · 对抗式 review(包裹A)**
- 机制(核心):按 tier 决定视角数 → Agent fan-out(默认怀疑)→ quorum 收敛 → 带 severity+confidence;**防相关性盲区**:视角互异,高危发现仍 `needs_human`。
- 领域包提供:`review_dimensions` + `review_prompts()`。

**④ Ratchet · 逃逸棘轮(支柱3)**
- 机制(核心):真漏过 → 开逃逸条目 → Agent 起草根因+候选检查 → 人确认 → 检查进注册表(`origin=ratchet`)→ `spawned_check` 非空才 close。**唯一「越用越紧」的复利模块,且领域无关。**
- 领域包提供:候选检查模板(可选)。

**⑤ ConformanceGov · 分层规格治理(§4.5)**
- 机制(核心):规格改动 → Agent 生成结构化影响分析供人审 → 维护**修订链 + 有效规格** → 矩阵每行 covered_by/waived → 否则 block;并跑**三类跨层漂移检测**(宪法↔修正案 / 修正案↔修正案 按 (a) 档 needs_human;规格↔代码 产 drift 条目)。
- 领域包提供:`spec_layers`、`parse_spec_requirements(layer,…)`、`resolve_effective_spec()`、`drift_board`。本领域强门禁;他人领域只读消费。

**⑥ RuntimeWatch · 运行时纵深防御(包裹B)**
- 机制(核心):接入运行时信号 → 断言命中 → 自动开 `EscapeRegistry` 草稿喂 ④;承载**规格↔代码(实然)漂移**信号。
- 领域包提供:`runtime_adapters()`(Cowboy 的 devnet/dispute window/WindTunnel)。执行器为**外部**设施,平台只消费信号。

**⑦ Metrics · 度量体系(§7)**
- 机制(核心):聚合 6 指标(逃逸率↓ / 发现时延↓ / 分层覆盖率 / conformance% / 棘轮增量 / 不变量门禁数),按 `domain_pack` 维度切分。
- 领域包提供:无(纯核心);前端可接 cowpilot dashboard 或独立页。

**依赖关系(演进顺序依据):**
```
①Classifier ─┬─→ ②InvariantGate ─┐
             └─→ ③ReviewOrch ────┤
                                  ├─→ GateDecision ─→ GitHub
⑤ConformanceGov ─────────────────┘
③/⑥ 的真漏过 ─→ ④Ratchet ─→ ②的注册表(复利闭环)
①②③④⑤⑥ 全部 ─→ ⑦Metrics
```
核心:**②不变量注册表 + ④棘轮 = 价值地基**(且天然领域无关);其余围其转;⑦度量横切。

---

## 6. 端到端数据流(🧑 = 人介入点)

### 流 A:普通 PR 生命周期
```
PR opened/sync → 适配层 NormalizedEvent → Orchestrator PR 状态机 [RECEIVED]
  → ① Classifier 定 tier(用领域包 classify_rules + 触及规格层)→ 写 Classifications
       ├─[低危]→ ③ 精简 review(1-2 视角)
       ├─[中危]→ ② 适用不变量子集 + ③ 多视角 review
       └─[高危]→ ② 全量适用不变量 + ③ 全视角 + 标记需 owner 签字
  [DISPATCHED] 并行:②→CI 执行器  ‖  ③→Agent Worker  (各回传 StructuredResult)
  [COLLECTING] barrier 汇总 → GateDecision
       ├─ 任一 active 不变量失败              → block
       ├─ 高危 + confirmed 高 severity 发现   → needs_human 🧑
       └─ 否则                               → pass
  → 回写 Check Run + inline comment + (高危)merge queue 准入 → 写 AuditLog + 喂 ⑦
```

### 流 B:分层规格改动(优先级最高)
```
规格文件变更(领域包某 spec_layer.source 命中)→ ① 按层定 tier(宪法=最高)→ ⑤ ConformanceGov 接管
  → Agent Worker 生成「结构化影响分析」草稿(动了哪条有效规格 / 影响哪些 repo / 兼容性 / 安全)
  → ⑤ 跨层漂移检测:
       ├─ 该改动是「合法修订」(显式声明覆盖宪法/旧CIP条款)？→ 更新修订链
       └─ 还是「静默抵触」？→ 高 severity finding + needs_human 🧑 (治理 a 档, 不自动 block)
  → 🧑 人审「影响地图」+ 漂移裁决(审地图 ≠ 审所有代码)
  → ⑤ 更新 ConformanceMatrix(含 source_layer/amends/effective):每条 requirement → covered_by 或 waived
  → 硬门禁:存在 gap 行 → block；联动更新 drift_board
  → pass 后,新有效规格的不变量进 ② 门禁池,后续所有 PR 自动受约束
```

### 流 C:逃逸 → 棘轮(唯一复利闭环,领域无关)
```
真漏过(线上 / ⑥ 运行时断言 / 事后 review)→ ④ 开 EscapeRegistry [OPEN]
  → Agent Worker 起草:根因分类 + 候选永久检查
  → 🧑 人确认根因 + 选定检查
  → 落地:不变量/测试 → InvariantRegistry(origin=ratchet, escape_id)→ 并入 ② 门禁池
          review 规则 → 更新领域包对应视角 prompt(可追溯 escape_id)
  → spawned_check 非空 → [CLOSED](数据库级约束)→ 棘轮增量 +1 喂 ⑦
```

---

## 7. 错误处理 / 失败策略 / 人工介入

### 7.1 按 tier 决定 fail-open vs fail-closed
| 场景 | 高危 | 低危 |
|---|---|---|
| 不变量门禁跑不起来 / CI 异常 | **fail-closed** → block 或 needs_human | fail-open → pass + degraded |
| Agent Worker 超时/超预算/崩溃 | **降级但不谎报** → needs_human + degraded | fail-open + degraded |
| 大脑/知识核不可用 | 适配层缓存事件,恢复后幂等重放 | 同 |

核心原则:**degraded 状态必须显式回写**,绝不让「没审成」伪装成「审过了」(呼应方法论 §4 防相关性盲区;沿用 audit-bot degraded 范式并上升为硬约束)。

### 7.2 幂等 / 预算 / 重试
- **幂等**:webhook 会重投 → 按 `change_ref + diff content-hash` 去重。
- **预算**:每 PR Agent review 设 token 上限(`DispatchJob.budget`),超额降级;成本记入 `StructuredResult.cost`。
- **重试**:执行器无状态,失败退避重试;CI flake 与真失败区分。

### 7.3 人工介入与申诉
- **needs_human 放行**:高危 confirmed 高 severity / 跨层漂移裁决 → owner 经 PR review 签字,入 AuditLog。
- **豁免/override**:可显式 waive,但必须带 reason+owner、入审计、**可设过期**;constitutional 不变量不可豁免。
- **误报申诉**:被判误报的发现回流改进对应视角 prompt。**误报 ≠ 逃逸**,不进棘轮。

---

## 8. 技术栈选型(绿地,贴合生态)

| 层 | 选型 | 理由 |
|---|---|---|
| 大脑核心 | **Python + FastAPI** | 与 audit-bot/cowpilot 同生态、团队熟;Agent SDK 原生 Python |
| 领域包 | **Python 包**(实现领域包契约接口)+ 声明式 manifest(YAML/JSON) | A 档:编译进发行物;`cowboy-pack` 为第一个;接口稳定,将来可改为动态加载 |
| 智能 | **Claude Agent SDK**(provider 薄接口包一层) | fan-out / 结构化输出 / prompt caching 开箱即用 |
| 知识核 | **PostgreSQL** | 注册表/矩阵/登记册是强关系数据;AuditLog 同库 append-only |
| 流动契约 | **JSON Schema 为单一真相**,生成 pydantic(大脑)+ Rust serde(执行器) | 契约跨 Python/Rust,语言中立、可生成,避免双份漂移 |
| CI 执行器 | 各 repo 原生(Cowboy 用 Rust proptest)+ 薄 reporter 发 StructuredResult | 不变量是测试,在各 repo CI 跑;reporter 与语言/项目无关 |
| 适配层 | **GitHub App**(Webhook + Checks API + merge queue) | 第一接入层;适配器框架领域无关 |

注:**核心代码与领域包分属不同 Python 包**(如 `marshal_core` / `marshal_pack_cowboy`),CI 校验核心包不依赖任何领域包(防领域知识泄漏进核心,呼应 §4.4 lint 约束)。

---

## 9. 平台如何自测(吃狗粮)

- **单元**:每个大脑模块独立测。
- **契约测试**:4 个流动 schema 双语言 round-trip(pydantic ⇄ serde 字节级一致)——平台先用在自己身上。
- **领域包契约测试**:用一个**最小假领域包**(`fake-pack`,含一个 2 层假规格体系)跑通核心全流程,证明核心不依赖 `cowboy-pack` 的任何具体内容(通用性 + 分层引擎的回归保障)。
- **集成**:录制真实 `NormalizedEvent` → 用假执行器重放过大脑,断言状态机迁移与 GateDecision 正确。
- **狗粮**:平台代码自身接入平台门禁(`marshal-pack` 自描述领域包)。

---

## 10. 演进建造顺序(先影子后强制;核心/领域从第 0 步就分离)

```
0. 知识核骨架 + 最小大脑 + GitHub 适配器 + 领域包接口骨架 + 空壳 cowboy-pack【只读/影子模式】
1. ②InvariantGate + ④Ratchet   ← 价值地基, cowboy-pack 接第一个切入点的不变量
       (跨 repo tx 编码 vectors  或  经济守恒 proptests)
2. ①Classifier + 小PR/路由        ← 打开分级(规则来自 cowboy-pack)
3. ③ReviewOrch(对抗 agent)       ← 先只产发现, 不阻断
4. ⑤ConformanceGov(分层规格 + 矩阵 + 跨层漂移)  ← 需流程共识
5. ⑥RuntimeWatch + ⑦Metrics       ← 持续
(动态插件运行时 = 待第二个领域包出现才启动, 不在本路线图)
```

**贯穿全程的安全模式:每个门禁先跑「影子模式」**(只评论、不阻断,观察误报率)→ 调稳后提升为 required status。

> 通用性的「免费验证」:第 0 步起就有 `fake-pack` 契约测试守护核心纯净——通用性不是将来的承诺,而是每次 CI 都被回归的属性。

---

## 11. 范围边界与非目标

**在范围内:**
- 领域无关核心(7 子系统机制、知识核、GitHub 接入、执行器调度、分层规格引擎)。
- 领域包接口 + 内建 `cowboy-pack`。
- 本团队负责的 repo/CIP 的强门禁。

**明确非目标(本 spec 不含):**
- **动态插件运行时 / 领域包 SDK / 领域包市场 / 跨领域计费**(D7-A:待第二个真实领域包出现再做)。
- 不拥有/不实现 devnet fuzzing、WindTunnel 等运行时设施本身(⑥ 只消费信号)。
- 不替代各 repo 的 CI 系统。
- 不强推门禁给其他团队负责的 CIP。
- **不替人裁决治理冲突**(跨层漂移按 (a) 档只标记+needs_human,合法性由人定)。
- 第一期不实现 GitHub 以外的接入(CLI/IDE/其他 forge 只留接口)。
- 各子系统的可开工详细 spec 后续各自立项。

---

## 12. 未决问题 / 风险

1. **影子→强制的晋级判据**:误报率低于多少、覆盖跑满多久才提升为 required?第一个切入点落地时用真实数据定阈值。
2. **introduced_at 估算**:逃逸引入时间难精确;初期用 git blame 近似。
3. **Agent review 成本**:高危全视角 fan-out 的 token 成本需实测;预算上限默认值待定。
4. **跨团队边界治理**:他人 CIP「只读消费」的具体协议需跨团队对齐(放 ⑤ 落地阶段)。
5. **领域包契约的稳定性**:A 档下接口只被 `cowboy-pack` 一个实现验证,可能有「为单一实现量身」的隐性耦合;`fake-pack` 契约测试缓解但不根治——第二个真实领域包接入时预期会调整接口,这是可接受代价。
6. **有效规格解析的准确性**:`resolve_effective_spec()` 把宪法套用修正案得出生效规格,自动化难度高(修正案常是自然语言)。初期可能需人工辅助标注修订链;全自动解析是渐进目标。
7. **动态插件运行时的触发条件**:何时从「编译进发行物」升级为「动态加载」留待第二领域包需求决定。
8. **大脑单体的拆分时机**:何时把某模块提升为独立服务留待运行数据决定。

---

## 修订记录
- **2026-06-01 v1** — 五节设计经交互评审通过后整合成文。
- **2026-06-01 v1.1** — 按 D7-A 增补通用化架构:核心领域无关 + 领域包契约(§4),Cowboy 降为第一领域包;各节标注领域包注入点;新增 `fake-pack` 通用性回归测试。
- **2026-06-01 v1.2** — 按 D8 增补分层规格体系(§4.5):宪法/修正案/代码三层 + 规范轴与描述轴双权威序;`spec_system`→`spec_layers`;ConformanceMatrix 加 source_layer/amends/effective;新增有效规格解析与三类跨层漂移检测;治理 (a) 档(静默抵触→needs_human 不自动 block);constitutional 级不变量不可豁免。
- **2026-06-01 v1.3** — 增补领域知识的获取与生长(§4.6):评审三层叠加(静态包+累积态+JIT)、冷启动三来源(约定探测/AI 发现/人工策展)、薄包+棘轮生长、DB⇄包晋升回路、领域包自身过门禁。
