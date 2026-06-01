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
