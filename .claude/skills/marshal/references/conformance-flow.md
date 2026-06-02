# 流 B — 分层规格治理 / conformance(⑤ ConformanceGov)

规格是放大器:一个有缺陷的 CIP 复制进多个实现(方法论 §1.3/§5)。本流程把 spec 改动与覆盖度暴露出来。当前是**可用骨架**,非全套(effective-spec 解析 / 逐条 requirement↔invariant 精确映射仍是后续)。

规格源(`reference_cowboy_spec_sources`):cowboy 仓库 `docs/cips/cip-N-*.md`(CIP=修正案)、`docs/whitepaper/*.md`(白皮书=宪法)。本地 `<workspace>/cowboy`(默认 `/home/ubuntu/workspace/cowboy`)。

## `/marshal conformance` — 符合度报告
```
"$PY" -m marshal_core.cli conformance --spec-root <workspace>/cowboy
```
输出:
- `cip_conformance_pct`、`cip_covered`/`cip_total`:有≥1 条不变量引用的 CIP 比例。
- `per_cip`(按最该补的网洞排序:无不变量 + MUST 越多越前):每 CIP `{must_requirements, invariants, covered}`。
- 终端摘要:打印 conformance% + top 欠覆盖 CIP。**诚实声明**:这是 CIP 级覆盖压力,不是逐条 requirement 已验证;MUST 计数为启发式抽取(代码块/示例会高估)。

## diff 命中规格层 → 叠加在流 A 上
1. 判定层:路径在 `docs/whitepaper/**` = **宪法改动 → 最高 tier、最广签字、`needs_human`**;在 `docs/cips/cip-N-*.md` = 修正案 → 高 tier。
2. 对改动的 CIP 抽 requirement:
   ```
   "$PY" -m marshal_core.cli spec-requirements --ref CIP-N --spec-root <workspace>/cowboy
   ```
   得 `{counts:{must,should,may}, requirements:[...]}`。
3. 查覆盖:`cli conformance` 的 `per_cip` 看这条 CIP 现有几条不变量;**新增/接口变更 CIP 却 0 不变量** → 标 gap,提议走流 C 开棘轮补一条(或显式 waive 并记原因)。
4. 生成结构化影响分析草稿供人审(动了哪些 requirement / 影响哪些 repo / 兼容性 / 安全),**人只审这张影响地图**,别逐行审所有相关代码(§5)。

## 治理红线(只标记,不替人裁)
- 某 CIP 实质抵触白皮书却未声明修订、或两 CIP 互相矛盾 → 高 severity `needs_human`,**不自动 block**(治理 a 档:合法演进还是越权,留人定;Marshal 只负责让它无法被忽略)。
- 源自宪法的 requirement 所派生的不变量标 **constitutional 级、最不可豁免**。
