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

## 执行
- 优先复用 `/code-review ultra`(云端多 agent)做高危层 review。
- 若 `/code-review ultra` 不可用或超预算 → 降级为本地少视角 review,并**显式标 degraded**。
- 把确认的高 severity 发现汇入流 A 的 GateDecision;如发生在已合并代码,提议转流 C。
