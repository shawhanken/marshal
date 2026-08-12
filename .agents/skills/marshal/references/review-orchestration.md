# ③ 对抗式 review 编排

## 视角 = tier 基集 ∪ 路径触发(classify 已给 review_dimensions,直接照单派)
tier 定**基集**(有序前缀):
- high: correctness, spec, cross-repo, security, econ, determinism(全 6)
- mid: correctness, spec, cross-repo(前 3)
- low: correctness(1)

**路径触发视角**(命中 diff 路径子串即并入,不看 tier;补基集会漏的横切面):
- `consensus-surface`:碰 receipt/`_root`/digest/event/logs/extra_data → 问"是否变更进入共识哈希的字节(state/receipt/logs_root、block digest)、是否 flag-day"。
- `test-validity`:碰测试文件(`/tests/`、`test_`、`_test.`、`tests.rs`)→ 对 PR 自带测试做心智 mutation,查 false-green / 只覆盖部分形态。

纯附加、去重,绝不删基集视角。这些都是 pack 数据(`REVIEW_DIMENSIONS` + `PATH_REVIEW_DIMENSIONS`),core 只收 `review_dimensions` 名单。

## 原则
1. **对抗式而非背书式**:prompt 是"找出这个改动会怎样出错/违反哪条 CIP 不变量",默认怀疑。
2. **防相关性盲区**:视角互异;靠分歧和 quorum 标问题。高危发现即便 review 全绿也保留 escalate。
3. **结构化输出**:每条发现带 {dimension, severity(low/mid/high), confidence, location}。

## spec 视角:JIT 读取被引用的规格正文
「spec」视角不能空喊"是否偏离 CIP 意图"——要把被引用 CIP/白皮书的**正文**读进来当依据:
1. 收集本次改动相关的 `spec_ref`(来自命中的不变量/契约的 `spec_ref`,或 PR 关联的 CIP label)。
2. 对每个 ref 调 `"$PY" -m marshal_core.cli spec-source --ref <CIP-N|WP>` → 得 `{repo, path_glob}`;`source` 为 null 的(如 C-1/M-B)无正文源,跳过。
3. 把 glob 落到工作区:`<workspace>/<repo>/<path_glob>`(Cowboy 规格源在 `cowboy` 仓库,本地 `/home/ubuntu/workspace/cowboy`;远端 github.com/cowboyinc/cowboy `docs/{cips,whitepaper}`)。`ls`/glob 命中后**读该文件**,喂给 spec 视角的 agent。
4. 读不到(本地无 cowboy clone / glob 未命中)→ spec 视角**标 degraded**,别假装比对过。

## 执行(多视角 fan-out → quorum 收敛)
1. 按 `review_dimensions` 使用 Codex subagent 能力**并行派出每视角一个 subagent**(各自默认怀疑、独立、视角互异)。若当前会话没有 subagent 能力,顺序执行同一组 lens；不允许静默省略。每个 agent 返回结构化发现:`{file, line, dimension, severity(low|mid|high), source:<lens名>, title}`。
2. 把所有发现汇成 JSON,过 quorum 聚合器:
   ```
   "$PY" -m marshal_core.cli review-quorum --findings-json '<findings>' [--quorum 2] [--proximity 10]
   ```
   → `{groups, escalate, confirmed, advisory, dropped, review_verdict}`。按 file+行邻近聚类,dimension 不进键。任一高危→escalate;不同视角数达 quorum→confirmed;单源中危→advisory;单源低危→丢弃。**advisory 必须列入报告,但不进入 skeptic gauntlet。**
3. **对抗式验证二段(抬高误报地板)**:对 quorum 后存活的每条发现(`confirmed` + `escalate`),先取互异 refute 视角:
   ```
   "$PY" -m marshal_core.cli refute-lenses --count <N>
   ```
   每个 skeptic 绑定一条 lens prompt,默认 refute,各返回 `{key, refuted:bool, reason, lens}`。汇成投票过:
   ```
   "$PY" -m marshal_core.cli review-verify --votes-json '[{"key":...,"severity":...,"votes":[{"refuted":true,"lens":"reachability"},...]}]'
   ```
   → `{survived, killed, unverified, verdict}`。规则:**仅严格多数 uphold 才存活**;平票/多数 refute → 杀(似是而非的误报被砍);无投票 → unverified(degraded,保留待人看)。

   （⑧ review trace，默认启用）审前用 `review-run-open --change-ref <sha> ... --expected-lenses-json '[...]' --expected-commands-json '[...]' --expected-external-scans-json '[...]'` 保存 `run_id` 和不可变的审计计划；上面 `review-verify` 追加 `--run-id <id> --findings-json '[{key,title,claim,location,lens},…]'` 落库。用户终审必须在 close 之前用 `finding-verdict` 补录；关闭后的 run（包括 finding verdict）不可再写入。
   审计完成后用 `review-run-close --run-id <id> --status complete|degraded --evidence-json <manifest>` 关闭 trace，并用 `review-run-show --run-id <id>` 回读。manifest 至少记录 head/base/tree、platform/worktree/toolchain/context_ref、closure/scout/prove/invariant 状态、计划内 expected/returned/missing lenses、计划内命令和外部扫描状态。
   `complete` 只允许所有步骤、计划内命令、预定 lens 和外部扫描都已完成；缺失 lens、失败或不可用资源必须 `degraded`。外部扫描 `unavailable`/`degraded` 时省略 `findings`（或置 null），绝不能写成 `findings: 0`；只有实际完成的扫描才可记录整数 findings。两种 close 状态都必须使用 closure/scout/prove/invariant 四阶段和 open 时的计划；complete 还必须有 40/64 位十六进制 head/base/tree SHA、完整复现字段、argv/整数 exit_code/log_ref；pass 必须 exit 0 且无失败测试；关闭后不得重新写入。
4. 最终汇入流 A 第 5 步:用 `review-verify` 的 `verdict` 与 `survived`(`killed` 不再上报,但**误报回流改进对应视角 prompt**,误报≠逃逸不进棘轮)。
5. 必须等待全部预定 lens 返回再聚合。任何 lens 崩溃或超时都标 `degraded(lens-incomplete)`,verdict 至少 escalate。
- 如存活的高 severity 发现落在**已合并代码**,提议转流 C(棘轮)。
