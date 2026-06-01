# 流 C — 逃逸棘轮细节

棘轮是唯一"越用越紧"的复利机制。每个真漏过 → 至少一条永久检查进注册表。

## 入口
- 手动:`/marshal ratchet "<bug 描述>"`
- 自动晋升:流 A 在**已合并代码**上确认高 severity 发现 → 问用户是否开逃逸。

## 步骤
1. 选 escape_id(如 `esc-0007`;可先 `cli` 无对应 list 命令时用日期+序号约定)。
   `"$PY" -m marshal_core.cli ratchet-open --escape-id <id> --desc "<bug>" --root-cause <class> [--change-ref <sha>]`
2. 起草候选永久检查 — 必须是可落地的断言,不是文档:
   - 它该是哪个 repo 的哪条 proptest/conformance-vector?
   - location_path / location_test / run_command 各是什么?
   - InvariantDef 字段:id, domain, spec_ref, executor_kind, location_repo, location_path, location_test, severity。
3. 把根因分类 + 候选检查摆给用户,**等确认**。
4. `"$PY" -m marshal_core.cli ratchet-close --escape-id <id> --spawned-check <inv-id> --inv-json '<上面 InvariantDef 的 JSON>'`
   - 缺 spawned_check 会被 CLI 拒绝 — 这是纪律,不要绕。
5. 去 `<location_repo>` 起草这条 proptest 的测试骨架(让用户/后续把它写实)。

## 根因分类参考(root_cause_class)
determinism-gap / econ-conservation / cross-repo-contract / state-consensus / auth / input-validation。
