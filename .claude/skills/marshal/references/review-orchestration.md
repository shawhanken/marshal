# ③ 对抗式 review 编排

## 视角 = tier 基集 ∪ 路径触发(classify 已给 review_dimensions,直接照单派)
tier 定**基集**(有序前缀):
- high: correctness, spec, cross-repo, security, econ, determinism(全 6)
- mid: correctness, spec, cross-repo(前 3)
- low: correctness(1)

**路径触发视角**(命中 diff 路径子串即并入,不看 tier;补基集会漏的横切面):
- `consensus-surface`:碰 receipt/`_root`/digest/event/logs/extra_data → 问"是否变更进入共识哈希的字节(state/receipt/logs_root、block digest)、是否 flag-day"。**专治 mid 档改到 receipt/事件组装却拿不到共识面**(否则静默分叉类改动没人问)。
- `test-validity`:碰测试文件(`/tests/`、`test_`、`_test.`、`tests.rs`)→ 对 PR 自带测试做心智 mutation,查 false-green / 只覆盖部分形态。

纯附加、去重,绝不删基集视角(只增覆盖不减)。availability/DoS 暂折进 `security` prompt(资源无界→单笔放大停机),待有干净路径触发器再拆独立视角。这些都是 pack 数据(`REVIEW_DIMENSIONS` + `PATH_REVIEW_DIMENSIONS`),core 只收 `review_dimensions` 名单——换 pack 即换视角集。

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
1. 按 `review_dimensions` **并行派出每视角一个 subagent**(各自默认怀疑、独立、视角互异;可用不同模型增大多样性)。每个 agent 返回结构化发现:`{file, line, dimension, severity(low|mid|high), source:<lens名>, title}`。
2. 把所有发现汇成 JSON,过 quorum 聚合器:
   ```
   "$PY" -m marshal_core.cli review-quorum --findings-json '<findings>' [--quorum 2] [--proximity 10]
   ```
   → `{groups, escalate, confirmed, advisory, dropped, review_verdict}`。计票**按 file+行邻近聚类**(同文件内相邻发现行距 ≤`proximity` 即并入一组,dimension 不进键)——**修复旧 `file:line:dimension` 精确键的漏检**:同一 bug 被不同视角报在略不同行/不同 dimension 时旧键永不合并、confirmed 恒 0,真阳性全靠「高危→escalate」逃生、单源中危被当噪声杀。规则:**任一高危→escalate(终审归人,哪怕单视角)**;不同视角数达 quorum→confirmed;**单源中危→advisory(浮出为建议,不丢)**;单源低危→当噪声丢弃。
   - **advisory 必须在报告里列出**(单视角未达 quorum 的中危观察),它是给人看的线索,不阻断、**不进下面第 3 步的对抗验证 gauntlet**(skeptic 会再把它杀掉,违背「不丢真阳性」初衷)。over-merge(proximity 太大把不同 bug 并一坨)比 under-merge 更糟;默认 10 偏保守,组内保留全部 titles 故不丢信息。
3. **对抗式验证二段(抬高误报地板)**:对 quorum 后存活的每条发现(`confirmed` + `escalate`),再派 N 个 skeptic subagent。**别派 N 个同质 skeptic**——同质=共同盲区;先取互异 refute 视角再逐个绑:
   ```
   "$PY" -m marshal_core.cli refute-lenses --count <N>
   ```
   → `{count, lenses:[{name, prompt}]}`(reachability / stale-basis / intended-design / severity / already-guarded;N>5 轮转复用)。每个 skeptic 用**分到的那条 lens 的 prompt**,统一保持「**默认 refute,除非有确凿证据证明该发现为真**」的立场,各返回 `{key, refuted:bool, reason, lens}`(带 `lens` 便于回流归因)。汇成投票过:
   ```
   "$PY" -m marshal_core.cli review-verify --votes-json '[{"key":...,"severity":...,"votes":[{"refuted":true,"lens":"reachability"},...]}]'
   ```
   → `{survived, killed, unverified, verdict}`。规则:**仅严格多数 uphold 才存活**;平票/多数 refute → 杀(似是而非的误报被砍);无投票 → unverified(degraded,保留待人看)。计票不看 lens(`review-verify` 仍纯数 refuted/uphold);lens 只为多样化提问 + 回流归因。

   (可选 ⑧ review trace)审前 `review-run-open --change-ref <sha> --repo <repo> --mode regular|deep --host claude --model <模型id>` 拿 `run_id`;上面 `review-verify` 追加 `--run-id <id> --findings-json '[{key,title,claim,location,lens},…]'` 即把每条发现的裁决链落库;用户终审后 `finding-verdict --finding-id <id> --verdict accepted|rejected|modified` 补录金标注。不带 `--run-id` 行为不变。
4. 最终汇入流 A 第 5 步:用 `review-verify` 的 `verdict` 与 `survived`(`killed` 不再上报,但**误报回流改进对应视角 prompt**,误报≠逃逸不进棘轮)。
5. 也可叠加 `/code-review ultra`(云端多 agent)作为额外一路视角喂进 quorum;它需人触发/计费,**skill 自己拉不起**——拉不到就少一路并**显式标 degraded**,绝不假装跑过。
- 如存活的高 severity 发现落在**已合并代码**,提议转流 C(棘轮)。
