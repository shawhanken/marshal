# AI 高速研发下的质量工程方法论(2026-05-29)

> **定位:** 方法论级文档(methodology),不是某个 CIP 的修复计划,也不是现状审计。它定义 Cowboy 在「AI 深度参与、多 repo、海量产出」条件下,如何用系统工程手段保证代码与规格质量,而**不依赖线性扩充人力**。
>
> **适用对象:** 全体 maintainer、CIP 作者、review gate 设计者、负责 `audit-bot` / `cowpilot` / CI 的工程师。
>
> **状态:** v1 草案 — 框架已定稿,落地路线图待团队认领与排期。

---

## 0. 一页纸结论(TL;DR)

**问题不是「review 跟不上」,而是用线性人工流程去对抗指数级生成产能,且人工 review 恰好抓不住 AI 代码的典型缺陷。**

任何「让人审得更快/更多/招更多人」的方案,都是在错误的维度上加码,投入越大、挫败越深。

正确的杠杆有三根支柱,外加两层包裹:

| | 支柱 / 层 | 一句话 | 杠杆 |
|---|---|---|---|
| 支柱 1 | **可执行的不变量门禁** | 把「深而本质」的正确性写成机器每次都跑的检查 | 最高,永久复利 |
| 支柱 2 | **风险分级的注意力分配** | 人只审机器无法替代的判断,按爆炸半径路由 | 高,立即见效 |
| 支柱 3 | **逃逸即学习的棘轮** | 每个漏过的深层 bug → 一条永久检查 | 复利来源 |
| 包裹 A | **AI 对抗式 review 层** | 用 AI 扩大覆盖面 + 聚焦人类注意力(不替代终审) | 中 |
| 包裹 B | **运行时纵深防御** | 承认 pre-merge 永不完美,在 devnet 抓逃逸 | 安全网 |

**最关键的判断:成败取决于支柱 1 与支柱 3 形成的棘轮。** 工具(`audit-bot` / `/code-review ultra`)只是载体;只上工具不建棘轮,半年后 AI reviewer 会给一切打绿灯,而深层 bug 照漏不误。

---

## 1. 问题定义:三个结构性不对称

现象:多 repo 庞大体量 + AI 海量产出 → 要么 review 来不及积压,要么草率 merge,深层问题潜伏后渗透进代码,发现时已晚。

但「现象」不是「根因」。根因是三个不对称,它们决定了为什么传统手段必然失败:

### 1.1 产能不对称:生成 O(海量) vs 人工验证 O(线性)
AI 生成是指数级、近乎免费的;人工逐行验证是线性、昂贵且会疲劳的。试图用扩充人力追平,是和一条指数曲线赛跑——数学上注定输。**这条不对称证伪了一切「在人工 review 吞吐维度上加码」的方案。**

### 1.2 缺陷类型不对称:AI 缺陷骗过人类依赖的信号
人工 review 高度依赖表层信号:能不能编译、读起来顺不顺、有没有过测试、风格像不像。AI 产出的代码**恰好在这些表层信号上表现优异**,却可能违反:

- **不变量**(invariant):如 gas 守恒、escrow 非负、nonce 单调;
- **跨 repo 契约**:如 `wallet` 的 tx 编码必须与 `node` 字节级一致;
- **规格语义**:实现「看起来对」,但偏离了 CIP 的真实意图。

这些「深而本质」的问题,**人眼逐行 review 本来就不擅长抓**——它们不在表层信号里。所以「审得更细」也救不了你:你是在用一把抓不住这类鱼的网,捞得再卖力也漏。

### 1.3 边界不对称:多 repo 放大盲区
Cowboy 是多 repo 组合体(`node` / `runner` / `cbfs` / `cbss` / `wallet` / `cowpilot` …),最致命的破坏发生在**跨边界**:

- `wallet` 改了 tx 编码 → 与 `node` 的反序列化不再字节兼容;
- CIP 定义的接口横跨 `node` + `runner` + `cbfs`,一侧改动破坏另一侧;
- 规格层(CIP)的一个缺陷,会**复制进多个实现**,危害远超单个 PR。

**任何单个 repo 的 CI、任何单个 reviewer,在设计上就看不到跨边界破坏。** 这是结构性盲区,不是注意力问题。

> **推论:** 既然根因是结构性不对称,解法也必须是结构性的——改变验证的执行主体(人→机器)、改变注意力的分配(全部→分级)、改变系统的学习方式(一次性→棘轮)。下面三支柱逐一对应。

---

## 2. 设计哲学(第一性原理)

在给出机制前,先确立四条贯穿始终的原则。后续每个机制都是这些原则的推论:

1. **能写成断言的,就不该靠人 review。** 机器检查不睡觉、不疲劳、不被漂亮 diff 蒙骗,且对未来所有 PR 永久生效。把验证从人迁移到机器,是把一次性的 review 成本转化为永久复利的资产。
2. **人类注意力是最稀缺资源,只投在不可替代的判断上。** 架构取舍、CIP 意图、新不变量的发现——这些 AI 暂时替代不了。逐行看 AI 写的样板代码不是。
3. **承认 pre-merge 永不完美,所以要纵深防御。** 不追求「一道门挡住一切」,而是多层网,让逃逸在 devnet(而非主网/用户)被抓住。
4. **系统必须从每次失败中变得更聪明。** 没有棘轮的质量体系会随时间退化;有棘轮的会随时间收紧。这是唯一能跟上指数产能的机制——因为它也是复利的。

---

## 3. 三支柱框架

### 支柱 1 — 可执行的不变量门禁(最高杠杆)

**核心洞察:** 那些「潜伏极深、本质性」的 bug,绝大多数是**某条没有被任何地方编码、因此无法被检查的不变量被违反了**。把它写成机器每次都跑的检查,就一次性把「review 负担」转成「CI 门禁」。

**为什么是最高杠杆:** 一条不变量写一次,永久对所有未来 PR 生效,且不依赖 reviewer 状态。这是把人的一次性劳动变成永久资产。

**Cowboy 具体不变量族(落地种子,见附录 A):**

- **经济 / Gas 守恒**:`burn + tip == 总费用`;escrow 永不为负;dual-meter(Cycles + Cells)计量与实际操作一致;`SettlementConfig` 三方比例(runner/burn/treasury)之和恒为 100%。→ 用 property-based test(`proptest`)而非逐行审 `verifier.rs` / `transaction.rs`。
- **跨 repo 契约(对 §1.3 盲区的正面打击)**:`wallet` 编码一笔 tx → `node` 必须解出**完全相同的字节**。维护一组 **golden vectors / conformance vectors**,放进**双方** CI;任一侧破坏契约立即红灯。CIP 定义的每个接口生成一套 conformance 测试。
- **PVM 确定性**:已有的 `validate_actor_code()` + determinism harness 持续加厚。注意已知逃逸——bare literal `2**10000` 绕过 `INT_GUARD_PREAMBLE`(VM 字节码路径)——这正是支柱 3 应织网盯死的典型(见 §5)。
- **状态 / 共识**:nonce 单调;Merkle root 在 propose / verify / report 三阶段一致;speculative 执行 rollback 后状态等价(已有嵌套 actor 调用 rollback 测试,应扩成不变量族)。

> **判定标准:凡是一个缺陷「本可被一条断言抓住」,它就不该进入人工 review 的职责范围。**

**与现有测试的区别:** 单元测试验证「某个具体输入产出某个具体输出」;不变量测试验证「**对所有输入**,某个性质恒成立」。AI 很容易写出通过具体单测、却违反全局性质的代码。这正是要补的层。

---

### 支柱 2 — 风险分级的注意力分配

**核心洞察:** 不是所有 PR 值得同等审查。用 AI 自动给每个 PR 打**爆炸半径(blast radius)**标签并路由,把稀缺的人类注意力集中到高危处。

**分级门禁(详见附录 B):**

| 层级 | 触发条件 | 门禁 |
|---|---|---|
| **高危** | 共识 / execution / gas / crypto / state-root / 跨 repo 契约 / CIP 定义接口 | 强制深审 + `/code-review ultra` 多 agent + ≥1 名领域 owner 人工签字 + 进 merge queue |
| **中危** | 普通 actor 逻辑、RPC handler | AI 对抗式 review + 1 名人工 review |
| **低危** | 文档、测试、工具脚本 | AI review 通过即可,人工抽查 |

**两条配套硬约束(从源头降低 review 成本):**

1. **小 PR 强制。** AI 倾向产出庞大 diff,而庞大 diff 不可审。拒绝 mega-PR,一个 PR 一个关注点。这是降低单次 review 成本最朴素也最有效的手段。
2. **高危改动先设计后写码。** 架构 / CIP 级别改动,先走 `superpowers:brainstorming` / 设计评审**再写代码**。否则你是在 review 一个建立在错误前提上的 2000 行实现——审得再细都是浪费。把判断点前移到「写码前」,成本低一个数量级。

---

### 支柱 3 — 逃逸即学习的棘轮(复利来源)

**核心洞察:** 每发现一个「潜伏很深」的 bug,**强制做一次轻量 post-mortem,产出至少一条永久检查**:一个回归测试、一条新不变量(回流支柱 1)、一条 lint、或一条 review agent 的 prompt 规则。

**为什么是复利来源:** 你被咬过的地方,网就在那里织得更密,同类问题再不可能静默通过。这是整个体系唯一能「随时间收紧而非退化」的机制。

**关键纪律:**
- `audit-bot` 的维度 agent prompt 必须**从真实逃逸里生长**,而不是凭空设计。每条 prompt 规则都应能追溯到一次真实的逃逸事件。
- post-mortem 的产物是「检查」,不是「文档」。一篇没人会再读的事后分析不构成棘轮;一条永久运行的断言才构成。
- 维护一个**逃逸登记册**(escape registry):记录每次逃逸 + 它对应织出的那条检查。这既是复盘资产,也是度量「网在哪里收紧了」的依据。

---

## 4. 包裹 A — AI 对抗式 review 层(连接三支柱)

已有自研的 `audit-bot`(5 维度 agent)和 Claude Code 平台的 `/code-review ultra`(旧别名 `/ultrareview`,云端多 agent review)。把它们正式做成 PR gate,喂给支柱 2 的分级。

**但必须避开一个致命陷阱:**

> **AI 审 AI 有相关性盲区(correlated blind spots)。** 同源模型会在同样的地方一起瞎,给你「全绿」的虚假安心。这比没有 review 更危险,因为它制造了已审查的错觉。

因此架构上强制:

1. **对抗式而非背书式。** Reviewer 的 prompt 是「找出这个改动会怎样出错 / 违反哪条 CIP 不变量」,默认怀疑,而非「看起来还行吗」。
2. **视角多样化。** 多 agent 用**不同视角**:正确性 / 规格符合 / 跨 repo 契约 / 安全 / 经济模型 / 确定性。靠它们之间的**分歧**和 quorum(多数票)来标记问题——冗余的同视角 agent 抓不到多样的失败模式。
3. **结构化输出,不替代终审。** 输出是「发现列表 + 严重度 + 置信度」,贴到 PR 上喂给分级路由。它的作用是**扩大覆盖面 + 抬高质量地板 + 帮人聚焦**,而**不是**替高危改动签字。高危的终审判断仍归人。

**定位总结:** AI review 层是支柱 2 的「输入端传感器」和支柱 1 的「补充」,不是独立的真理来源。

---

## 5. CIP 规格层治理(优先级最高的杠杆面)

规格是放大器:一个有缺陷的 CIP 会复制进多个实现,危害指数放大(§1.3)。所以规格层的门禁优先级高于代码层。

1. **CIP 改动必须附结构化影响分析。** 内容:动了哪些不变量、影响哪些 crate / repo、兼容性 / 迁移影响、安全含义。AI 极擅长这种 fan-out——让它生成初稿,**人只审这张「影响地图」**,比审所有相关代码便宜得多。
2. **维护 conformance matrix(符合性矩阵)。** 每条 CIP 要求 → 验证它的测试。gap 一目了然。
3. **硬门禁:CIP 不能 merge,直到其 conformance 测试存在**(或显式 waive 并在矩阵中记录原因 + 责任人)。这条把规格和验证强绑定,杜绝「规格写了但没人验」的静默漂移。

> 与 `refs/wiki/drift.md`(文档-代码漂移看板)联动:conformance matrix 是 drift 看板在「规格 ↔ 测试」维度的延伸。

---

## 6. 包裹 B — 运行时纵深防御

承认 pre-merge 永远不完美(原则 3),深层 bug 一定会漏过。目标是在**它渗透到主网 / 用户之前**抓住:

- devnet 上常驻 **fuzzing / 不变量断言 / 差分重放**(differential replay,对照参考实现)。把支柱 1 的不变量也部署成 devnet 运行时断言,而不只是 CI 测试。
- 复用 CIP-2 的运行时安全机制:**dispute window(`DISPUTE_WINDOW_BLOCKS = 75`)**、verification modes(MajorityVote / Deterministic / EconomicBond)本身就是「运行时校验 + 经济惩罚」的纵深防御。把这套哲学扩展到执行层。
- **金丝雀 / 灰度:** 高危改动先在 devnet 跑足够长的真实负载(参考 `benchmark/` WindTunnel)再上推。

---

## 7. 度量体系(你无法管理你不度量的东西)

放进 `cowpilot` dashboard,作为方法论是否生效的客观信号,也是说服团队投入门禁建设的弹药:

| 指标 | 定义 | 健康方向 |
|---|---|---|
| **逃逸率(escape rate)** | merge 后才发现的 bug / 总 bug 数 | ↓ 持续下降 |
| **平均发现时延** | bug 引入 → 发现的区块/天数 | ↓ 越短越好 |
| **风险分层 review 覆盖率** | 各层按门禁要求被审的比例 | 高危层 = 100% |
| **CIP conformance %** | 有测试覆盖的 CIP 要求 / 总要求 | ↑ 趋近 100% |
| **棘轮增量** | 每次逃逸新增的永久检查数 | ≥ 1 / 逃逸 |
| **不变量门禁数** | 支柱 1 累计断言数 | 单调增 |

---

## 8. 落地路线图(按杠杆排序,可增量推进)

每一步都可独立交付价值,不必等全套就绪。

1. **先建不变量门禁 + 逃逸棘轮(支柱 1 + 3)。** 最高复利,且天然增量——从「已经被咬过的地方」开始织网(如 bare literal `2**10000` 逃逸、gas 守恒)。
2. **风险分级路由 + 小 PR 约束(支柱 2)。** 立刻缓解「审不过来」。
3. **把 `audit-bot` / `/code-review ultra` 接成对抗式 PR gate(包裹 A)。** 注意防相关性盲区(§4)。
4. **CIP conformance matrix + 影响分析(§5)。** 规格层杠杆最大,但需流程共识,放在团队对前几步有信心之后。
5. **devnet 纵深防御 + 指标 dashboard(包裹 B + §7)** 持续推进。

> **建议:不要试图一次铺开全部。** 挑一个具体切入点先做出闭环验证——推荐「跨 repo tx 编码 conformance vectors」(直击 §1.3 盲区)或「经济守恒 property tests」(支柱 1 最干净的样板)。跑通一个,团队就有了信心和模板。

---

## 9. 反模式清单(明确不要做什么)

| 反模式 | 为什么错 |
|---|---|
| 「招更多人 / 审得更快」 | 在产能不对称的错误维度加码(§1.1) |
| 「审得更细就能抓住深层 bug」 | 人眼不擅长抓不变量/契约类缺陷,网型不对(§1.2) |
| 「上了 AI reviewer 就放心了」 | 相关性盲区制造虚假安心,比没审更危险(§4) |
| 「只写 post-mortem 文档」 | 没人再读的文档不是棘轮;只有永久检查才是(§3) |
| 「先把全套体系建好再上线」 | 违背增量交付;应挑一个切入点跑通闭环(§8) |
| 「CIP 先 merge,测试以后补」 | 规格层是放大器,静默漂移危害指数级(§5) |
| 「大 PR 一把梭」 | 庞大 diff 不可审,AI 尤其爱产出(§3 支柱 2) |

---

## 10. 与现有资产的映射

本方法论不要求从零造,而是把现有资产组装成闭环:

| 现有资产 | 在本方法论中的角色 |
|---|---|
| `audit-bot/`(5 维度 agent,GitHub Action + FastAPI)— **自研仓库资产** | 包裹 A 对抗式 review 层的执行体;从文档审计泛化到 PR/CIP 审计 |
| `/code-review ultra`(旧别名 `/ultrareview`)— **Claude Code 平台能力,非仓库代码** | 高危层(支柱 2)的强制多 agent 云端 review 门禁;用户手动触发、按量计费,需 git 仓库 |
| `cowpilot/`(CADP,MCP + dashboard) | §7 度量体系的承载面 |
| CIP 体系(`refs/cips/`) | §5 规格层治理的对象;conformance matrix 的来源 |
| `refs/wiki/drift.md` | 与 conformance matrix 联动的漂移看板 |
| `benchmark/`(WindTunnel) | 包裹 B 金丝雀 / 灰度的负载来源 |
| CIP-2 dispute window / verification modes | 包裹 B 运行时纵深防御的现成范式 |
| `superpowers:brainstorming`(Claude Code skill)— **平台能力,非仓库代码** | 支柱 2「先设计后写码」的执行工具 |

---

## 附录 A — 不变量种子清单(支柱 1 起点)

按域分类的初始断言候选,供落地时优先编码:

**经济 / Gas**
- `burn + tip == fee_total`(每 tx / 每 block)
- `escrow_balance >= 0` 恒成立(job_submit / settlement / cancel 全路径)
- `settlement.runner_percent + burn_percent + treasury_percent == 100`
- dual-meter:计费的 Cells == 实际 `key.len() + val.len()` 累计;Cycles == 实际操作累计
- basefee 更新单调性:EIP-1559 公式对 target 上/下偏离的方向正确

**状态 / 共识**
- account nonce 单调递增,无跳号
- Merkle root 在 propose / verify / report 三阶段对同一 batch 恒等
- speculative rollback 后状态 == 执行前状态(含嵌套 actor 调用)
- deferred tx:`pending_per_actor <= MAX_PENDING_DEFERRED_PER_ACTOR(64)`;age <= `DEFERRED_TX_MAX_AGE_BLOCKS(1000)`

**跨 repo 契约**
- `wallet` 编码 tx → `node` 解码字节级一致(golden vectors,双向)
- `runner` 的 `RateCard` / `RunnerResult` / `VerifierCheck` 与 `node/runner/src/types.rs` 序列化兼容
- CIP 定义接口的每个方法签名 + 语义 conformance

**PVM 确定性**
- 黑名单 import(`ctypes` / `_ctypes` / `cffi` / `_cffi_backend`)被拒
- 大整数:`int(2)**10000` 被 guard 拦截;**bare literal `2**10000` 已知逃逸 → 棘轮盯死项**
- `asyncio.gather` ban;1234-digit int limit
- 同一 actor + 同一输入 → 跨节点字节级相同输出

---

## 附录 B — 风险分级路由判定(支柱 2 实现要点)

自动分级器(可由 AI 跑在 PR 触发的 CI 中)按改动文件路径 + diff 内容打标:

- **判高危**(命中任一):
  - 路径含 `execution/{engine,transaction,system_instruction,basefee}` / `storage/{speculative,process_block}` / `chain/` 共识相关 / 任何 `crypto` / `*_root` 计算
  - 改动跨 ≥2 个 repo,或触碰 §1.3 列出的跨 repo 契约文件
  - PR 关联的 CIP 标签为「新增」或「接口变更」
  - 触碰系统 actor 地址逻辑(`0x06` / `0x09` / `0x91`–`0x95`)
- **判中危:** 普通 actor 逻辑、RPC handler、非契约型 runner 代码
- **判低危:** 仅 `*.md` / 测试 / 脚本 / 注释

分级结果作为 PR label,驱动 §3 支柱 2 表格的门禁要求。**分级器误判向上不向下**:不确定时升一级,宁可多审不可漏审。

---

## 修订记录

- **2026-05-29 v1** — 框架定稿(三支柱 + 两包裹 + 规格治理 + 度量 + 路线图)。落地路线图待认领排期。
