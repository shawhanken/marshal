# ③ 对抗式 review 编排

## 视角数由 tier 决定(classify 已给 review_dimensions)
- high: correctness, spec, cross-repo, security, econ, determinism(全 6)
- mid: correctness, spec, cross-repo(前 3)
- low: correctness(1)

## 原则
1. **对抗式而非背书式**:prompt 是"找出这个改动会怎样出错/违反哪条 CIP 不变量",默认怀疑。
2. **防相关性盲区**:视角互异;靠分歧和 quorum 标问题。高危发现即便 review 全绿也保留 needs_human。
3. **结构化输出**:每条发现带 {dimension, severity(low/mid/high), confidence, location}。

## spec 视角:JIT 读取被引用的规格正文
「spec」视角不能空喊"是否偏离 CIP 意图"——要把被引用 CIP/白皮书的**正文**读进来当依据:
1. 收集本次改动相关的 `spec_ref`(来自命中的不变量/契约的 `spec_ref`,或 PR 关联的 CIP label)。
2. 对每个 ref 调 `"$PY" -m marshal_core.cli spec-source --ref <CIP-N|WP>` → 得 `{repo, path_glob}`;`source` 为 null 的(如 C-1/M-B)无正文源,跳过。
3. 把 glob 落到工作区:`<workspace>/<repo>/<path_glob>`(Cowboy 规格源在 `cowboy` 仓库,本地 `/home/ubuntu/workspace/cowboy`;远端 github.com/cowboyinc/cowboy `docs/{cips,whitepaper}`)。`ls`/glob 命中后**读该文件**,喂给 spec 视角的 agent。
4. 读不到(本地无 cowboy clone / glob 未命中)→ spec 视角**标 degraded**,别假装比对过。

## 执行(多视角 fan-out → quorum 收敛)
1. 按 `review_dimensions` **并行派出每视角一个 subagent**(各自默认怀疑、独立、视角互异;可用不同模型增大多样性)。每个 agent 返回结构化发现:`{file, line, dimension, severity(low|mid|high), source:<lens名>, title}`。
2. 把所有发现汇成 JSON,过 quorum 聚合器:
   ```
   "$PY" -m marshal_core.cli review-quorum --findings-json '<findings>' [--quorum 2]
   ```
   → `{groups, needs_human, confirmed, dropped, review_verdict}`。规则:同 key(file:line:dimension)按**不同视角数**计票;达 quorum→confirmed;**任一高危→needs_human(终审归人,哪怕单视角)**;孤立低危→当噪声丢弃。
3. **对抗式验证二段(抬高误报地板)**:对 quorum 后存活的每条发现(`confirmed` + `needs_human`),再派 N 个 skeptic subagent,prompt 为「**默认 refute,除非有确凿证据证明该发现为真**」,各返回 `{key, refuted:bool, reason}`。汇成投票过:
   ```
   "$PY" -m marshal_core.cli review-verify --votes-json '[{"key":...,"severity":...,"votes":[{"refuted":true},...]}]'
   ```
   → `{survived, killed, unverified, verdict}`。规则:**仅严格多数 uphold 才存活**;平票/多数 refute → 杀(似是而非的误报被砍);无投票 → unverified(degraded,保留待人看)。
4. 最终汇入流 A 第 5 步:用 `review-verify` 的 `verdict` 与 `survived`(`killed` 不再上报,但**误报回流改进对应视角 prompt**,误报≠逃逸不进棘轮)。
5. 也可叠加 `/code-review ultra`(云端多 agent)作为额外一路视角喂进 quorum;它需人触发/计费,**skill 自己拉不起**——拉不到就少一路并**显式标 degraded**,绝不假装跑过。
- 如存活的高 severity 发现落在**已合并代码**,提议转流 C(棘轮)。
