# 流 C — 逃逸棘轮细节

棘轮是唯一"越用越紧"的复利机制。每个真漏过 → 至少一条永久检查进注册表。

## 入口
- 手动:`/marshal ratchet "<bug 描述>"`
- 自动晋升:流 A 在**已合并代码**上确认高 severity 发现 → 问用户是否开逃逸。

## 步骤
1. 选 escape_id(如 `esc-0007`;可先 `cli` 无对应 list 命令时用日期+序号约定)。
   `"$PY" -m marshal_core.cli ratchet-open --escape-id <id> --desc "<bug>" --root-cause <class> [--change-ref <sha>]`
2. **先定形状**(`cli`/pack 的 `ratchet_guidance(<root_cause>)`):否定性·对抗性属性
   (机密性 / IND-CPA / 保密 / 泄露 / 越权 / 旁路)**不可往返化**。对这类根因,一条功能
   往返 proptest 在脆弱构造上照样为绿,会造出"绿着却漏"的假覆盖(node #470 的教训:
   `crypto.cbss_ibe_roundtrip` 漏掉了 confidentiality break)。此时 permanent guard 应是
   一条 **review-lens 危险点**(pack 的 `SecurityHazard`,喂给 security review),而不是
   proptest;spawned_check 记成 `hazard:<id>` 而非不变量 id。
3. 若是功能/安全性属性(可往返化)→ 起草候选永久检查,必须是可落地的断言,不是文档:
   - 它该是哪个 repo 的哪条 proptest/conformance-vector?
   - location_path / location_test / run_command 各是什么?
   - InvariantDef 字段:id, domain, spec_ref, executor_kind, location_repo, location_path, location_test, severity。
3. 把根因分类 + 候选检查摆给用户,**等确认**。
4. `"$PY" -m marshal_core.cli ratchet-close --escape-id <id> --spawned-check <inv-id> --inv-json '<上面 InvariantDef 的 JSON>'`
   - 缺 spawned_check 会被 CLI 拒绝 — 这是纪律,不要绕。
5. 去 `<location_repo>` 起草这条 proptest 的测试骨架(让用户/后续把它写实)。

## 根因分类参考(root_cause_class)
可往返化(→ proptest):determinism-gap / econ-conservation / cross-repo-contract / state-consensus / input-validation。
**不可**往返化(→ review-hazard,见步骤 2):confidentiality / ind-cpa / secrecy / leak / exposure / auth / privilege / side-channel。
