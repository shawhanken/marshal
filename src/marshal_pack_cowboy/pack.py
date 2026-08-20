"""Cowboy 领域包 (第一个领域包)。本切片只含经济守恒不变量 + 极简分级规则。"""
import re
from dataclasses import dataclass
from marshal_core.domain_pack import InvariantDef
from marshal_pack_cowboy import ci_security

_HIGH_PREFIXES = (
    "execution/src/execution/engine",
    "execution/src/execution/transaction",
    "execution/src/execution/system_instruction",
    "execution/src/execution/basefee",
    "execution/src/runner/",
    "storage/src/speculative",
    "storage/src/process_block",
    "chain/",
)
_HIGH_SUBSTR = ("crypto", "_root")

# Per-repo 高危路径前缀 (repo -> (prefixes, reason))。node 用上面的 _HIGH_PREFIXES;
# cbss/cbfs 的核心安全/正确性面在此 —— 否则普通子串规则会把它们误判成 mid。
# 路径相对各自 repo 根:cbss 是 crates/…(workspace),cbfs 是扁平 crate 目录。
_REPO_HIGH_PREFIXES = {
    "cbss": (
        # threshold-IBE / DKG / 封缄 / 部分签名 / 密钥托管 (机密性+正确性双关键)
        ("crates/cbss-crypto/",
         "crates/cbssd/src/cip7_content_key_seal", "crates/cbssd/src/cip9_volume_seal",
         "crates/cbssd/src/partial_sign", "crates/cbssd/src/dkg_driver",
         "crates/cbssd/src/reshare_driver", "crates/cbssd/src/commonware_dkg",
         "crates/cbssd/src/chain_authorizer", "crates/cbssd/src/keychain",
         "crates/cbssd/src/share_storage"),
        "cbss threshold-IBE / DKG / key-custody surface (CIP-7/CIP-9/CIP-24)"),
    "cbfs": (
        # 机密性(crypto) / 数据可恢复性(erasure) / 持久性(placement) /
        # 访问控制(auth) / 完整性元数据(manifest) / 跨 repo 共享类型(cowboy-ras)
        ("crypto/", "erasure/", "placement/", "auth/", "manifest/", "cowboy-ras/"),
        "cbfs integrity/confidentiality surface (crypto/erasure/placement/auth/manifest)"),
    "cbqs": (
        # 交付正确性(at-least-once/终态生命周期/safe-prefix)+ 签名 receipt hash-chain
        # (gated on durable commit)+ StreamGrant 授权 + 权威 broker 状态 / chain 实例绑定。
        # broker 只存密文,机密性在 client/protocol,不在本 repo —— 故此处是「正确性+授权」面。
        ("crates/cbqsd/src/receipt", "crates/cbqsd/src/authorization",
         "crates/cbqsd/src/standard_append", "crates/cbqsd/src/standard_delivery",
         "crates/cbqsd/src/standard_group", "crates/cbqsd/src/standard_lane",
         "crates/cbqsd/src/broker_state", "crates/cbqsd/src/storage",
         "crates/cbqsd/src/transport/chain"),
        "cbqs delivery-correctness / signed-receipt-chain / StreamGrant-auth surface (CIP-39)"),
    "cowboy-protocol": (
        # 共识 wire 编解码 / 规范向量 / 共享类型 / CBSS 加密 —— 字节兼容性即共识:
        # 任何编码 drift = 两实现算出不同 tx hash / 接受不同字节 = 链分叉。
        ("crates/cowboy-protocol-codec/", "crates/cowboy-protocol-types/",
         "crates/cowboy-protocol-cbss-crypto/", "bindings/cowboy-crypto/"),
        "cowboy-protocol consensus codec / wire-types / crypto (byte-compat = consensus)"),
    "gateway": (
        # x402 支付:签名/编码/双花预留守卫 + 链上结算 + 凭证解析 —— 缺陷=重复结算/免费服务。
        ("crates/gateway-x402/", "crates/gateway-server/src/payment_state",
         "crates/gateway-server/src/chain_payments", "crates/gateway-server/src/x402",
         "crates/gateway-server/src/mpp", "crates/gateway-chain/"),
        "gateway x402 payment / settlement / chain / credential surface"),
    "runner": (
        # 结果验证(N-of-M 共识/确定性字节比对/罚没)+ 门限 BLS-VRF + 派工 + 沙箱隔离 +
        # TEE/密钥 —— 缺陷=认证错误 off-chain 结果 / 罚没诚实 runner / 逃逸沙箱。
        ("crates/result-verifier/", "crates/runner-consensus/",
         "crates/runner-node/src/canonical_result_parity", "crates/job-dispatcher/",
         "crates/runner-container/src/sandbox", "crates/runner-container/src/oci",
         "crates/runner-container/src/runc", "crates/tee-verifier/",
         "crates/runner-tee/", "crates/cowboy-nitro-signer/"),
        "runner verification / consensus / dispatch / sandbox / TEE-key surface (CIP-2)"),
}

_LOW_SUFFIXES = (".md",)
_LOW_SUBSTR = ("/tests/", "test_", "/scripts/", "tests.rs")

# CI / infra-as-code. A benign workflow edit (runner label, comment) is operational
# (low). The dangerous cases — untrusted trigger × {privileged runner | secret | write
# perm | open cross-repo dispatch} — are detected by combination over whole-file content
# in `ci_security` (drives escalation via security_hazards), NOT by a flat token scan
# (which mis-escalated #649 off an incidental `secrets.SLACK` context line, yet still
# missed the coverage.yml runner risk whose secret sat outside the diff window).
_CI_PREFIX = ".github/"
_SYS_ADDR_TOKENS = ("0x06", "0x09", "0x91", "0x92", "0x93", "0x94", "0x95")

REVIEW_DIMENSIONS = [
    {"name": "correctness", "prompt": "找出这个改动会怎样产生错误结果或破坏现有行为。"},
    {"name": "spec", "prompt": "实现是否偏离它所引用 CIP 的真实意图?指出语义漂移。"},
    {"name": "cross-repo", "prompt": "这个改动是否破坏跨 repo 契约(编码/类型序列化字节兼容)?"},
    {"name": "security", "prompt": ("默认怀疑:越权、未校验输入、可滥用路径;安全判定"
                                    "**勿信攻击者可控的 tx 字段**(chain_id/nonce/calldata);"
                                    "有无重放/新鲜度缺失(nonce/domain-separation),或资源"
                                    "无界(单笔可放大到停机/爆内存)?")},
    {"name": "econ", "prompt": "gas/费用/escrow 守恒是否被破坏?burn+tip==fee?escrow 非负?"},
    {"name": "determinism", "prompt": "PVM 确定性:有无非确定来源、绕过 int guard、黑名单 import?"},
]

# 路径条件触发的视角:命中 `when_paths`(子串匹配任一 diff 路径)即**并入** review_plan,
# 不看 tier —— 补 tier-only 基集会漏的横切面(如 mid 档改到 receipt 组装却拿不到共识面)。
# 纯附加、去重,绝不删基集视角(只增覆盖、不减)。当前 pack 只放两条真空白;availability
# 暂折进 security prompt(缺乏干净的路径触发器),待触发器成熟再拆独立视角。
PATH_REVIEW_DIMENSIONS = [
    # 共识面(领域视角,Cowboy 专属):改动是否变更进入某共识哈希的字节 →
    # state_root / receipt_root / logs_root / block digest?若是=可能静默分叉,需 flag-day。
    # 触发子串刻意比分级器的 `_root` 更宽,兜住 receipt/digest/event 组装这类 mid 档改动。
    {"name": "consensus-surface",
     "prompt": ("这个改动是否变更进入**共识哈希**的字节(state_root/receipt_root/"
                "logs_root/block digest)?含事件/结构化错误/extra_data 进 root。若是,"
                "是否构成 flag-day、需协调上线?"),
     "when_paths": ("receipt", "_root", "digest", "event", "logs", "extra_data")},
    # 证据对抗(元视角,universal-candidate:第二个 pack 出现时可上提 core):PR 自带
    # 测试是否 false-green?触发于改动碰测试文件时 —— 对新增/改动测试做心智 mutation。
    {"name": "test-validity",
     "prompt": ("假设本 PR 新增/改动的测试是 false-green:对被测代码做一次心智 mutation,"
                "测试还会过吗?它是否只覆盖了漏洞的部分形态(留下未测的绕过向量)?"),
     "when_paths": ("/tests/", "test_", "tests.rs", "_test.")},
]

_ECON_INVARIANTS = [
    InvariantDef(id="econ.fee_conservation", domain="econ", spec_ref="CIP-3",
                 executor_kind="proptest", location_repo="node",
                 location_path="execution/src/econ_invariants.rs",
                 location_test="econ_invariants::econ_fee_conservation", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-execution", "econ_invariants::econ_fee_conservation", "--", "--exact"]),
    InvariantDef(id="econ.settlement_sum_100", domain="econ", spec_ref="CIP-2",
                 executor_kind="proptest", location_repo="node",
                 location_path="execution/src/econ_invariants.rs",
                 location_test="econ_invariants::econ_settlement_sum_100", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-execution", "econ_invariants::econ_settlement_sum_100", "--", "--exact"]),
    InvariantDef(id="econ.escrow_non_negative", domain="econ", spec_ref="CIP-2",
                 executor_kind="proptest", location_repo="node",
                 location_path="execution/src/econ_invariants.rs",
                 location_test="econ_invariants::econ_escrow_non_negative", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-execution", "econ_invariants::econ_escrow_non_negative", "--", "--exact"]),
    InvariantDef(id="econ.tx_fee_conservation", domain="econ", spec_ref="CIP-3",
                 executor_kind="proptest", location_repo="node",
                 location_path="execution/src/econ_invariants.rs",
                 location_test="econ_invariants::econ_tx_fee_conservation", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-execution", "econ_invariants::econ_tx_fee_conservation", "--", "--exact"]),
    # Ratchet esc-20260727-gas-waterfall-alias-mint (escaped bugs H2/H-2, surfaced+fixed
    # by node #1142): execute_transaction snapshots the sender/actor BEFORE the fee
    # waterfall and writes it back after; a tier (card->actor->owner) that writes an
    # account ALIASING that snapshot has its debit erased while burn+tip carry it (CBY
    # mint). Confirmed: actor==0x16 (token-card) and UseOwnerBalance owner==tx.from.
    # Property: a full execute_transaction gas cascade conserves Σ CBY == burn+tip whether
    # the sponsor aliases the sender or is a third party. proptest! macro → `-- --exact`
    # on the full module path.
    InvariantDef(id="econ.fee_waterfall_alias_conservation", domain="econ", spec_ref="CIP-28",
                 executor_kind="proptest", location_repo="node",
                 location_path="execution/src/execution/tests.rs",
                 location_test="execution::tests::econ_waterfall_alias_conservation::econ_fee_waterfall_alias_conservation", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-execution", "execution::tests::econ_waterfall_alias_conservation::econ_fee_waterfall_alias_conservation", "--", "--exact"]),
    # Ratchet esc-20260605-timer-burn (escaped bug surfaced+fixed by node #580):
    # a duplicate refund block double-credited the timer fee-payer (silent mint)
    # and the burn was accumulated after take_block_fees() drained it, so it never
    # reached the Address::ZERO sink. Conservation: for a timer-firing block,
    # tokens debited from fee-payers == tokens credited to Address::ZERO.
    # Lives in the storage crate as a module-nested #[test] sweep (NOT a proptest!
    # macro), so executor_kind="test" and run_command uses a substring filter
    # (NOT `-- --exact`), matching the _STATE family convention for this file.
    InvariantDef(id="econ.timer_burn_conservation", domain="econ", spec_ref="CIP-3",
                 executor_kind="test", location_repo="node",
                 location_path="storage/src/state_invariants.rs",
                 location_test="state_invariants::tests::prop_timer_burn_conservation", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-storage", "prop_timer_burn_conservation"]),
]


@dataclass
class Contract:
    id: str
    repos: list[str]
    trigger_paths: dict[str, list[str]]   # repo -> 路径前缀列表
    verify_invariants: list[str]


CONTRACTS = [
    Contract(id="tx-encoding", repos=["wallet", "node"],
             trigger_paths={"wallet": ["src/lib/cbor", "src/lib/codec"],
                            "node": ["types/src/execution"]},
             verify_invariants=["contract.tx_encoding_roundtrip",
                                "contract.sys_opcode_uniqueness"]),
    Contract(id="runner-types", repos=["runner", "node"],
             trigger_paths={"runner": ["crates/runner-common/src/types"],
                            "node": ["runner/src/types"]},
             verify_invariants=["contract.runner_types_serde"]),
    # CIP-9 RAS: 共享 crate cowboy-ras 的元数据/存储键线格式. 权威源住 node/ras/
    # (package cowboy-ras, node 内 workspace 成员, 由 node/runner/cbfs 共依). 独立
    # cowboy-ras/ repo 已下架 (2026-07); cbfs 不再 vendored RAS 类型, 改依赖 node crate,
    # 故 cbfs 触发只留 manifest/src (原 "cowboy-ras/src" 已无对应路径, 移除).
    # 任一侧动 RAS 类型/存储键 → 必须重跑 node 侧锁定金标准哈希向量, 否则跨 repo
    # 元数据线格式漂移 (CIP-9 §存储布局).
    Contract(id="cip9-ras", repos=["cbfs", "node"],
             trigger_paths={"cbfs": ["manifest/src"],
                            "node": ["ras/src/types", "ras/src/storage_keys",
                                     "ras/src/test_vectors"]},
             verify_invariants=["contract.ras_canonical_vectors"]),
    # CIP-24 CBSS: 秘密释放管线的链上线格式. cbss daemon 的链类型/授权 (cbss-types,
    # cbssd chain_tx/chain_authorizer) ↔ node types/src/cbss.rs (cowboy-types).
    # 任一侧动这些类型 → 重跑 node 侧 CBSS 编码往返族 (release_request_body 为代表)。
    Contract(id="cip24-cbss", repos=["cbss", "node"],
             trigger_paths={"cbss": ["crates/cbss-types/src",
                                     "crates/cbssd/src/chain_tx",
                                     "crates/cbssd/src/chain_authorizer"],
                            "node": ["types/src/cbss"]},
             verify_invariants=["contract.cbss_wire_round_trip"]),
    # System-actor address allocation: the WP §9.1 address space is allocated by
    # spec (whitepaper/CIP) and pinned in code (node runner/src/system_actors.rs +
    # types/src/constants.rs). Address collisions are a recurring class (WP 0x0D
    # addrmap, CIP-16 0x14, CIP-34 0x14). Grown via escape
    # esc-20260629-sys-addr-alloc-unverified: the opcode side already had
    # contract.sys_opcode_uniqueness, but the address side's real uniqueness test
    # (cowboy-runner addresses_are_unique) was UNREGISTERED — cowboy#211's 0x14
    # allocation was verified entirely by hand. Any PR touching the address
    # constants OR the spec address table re-runs the uniqueness backstop.
    Contract(id="sys-actor-addr", repos=["node", "cowboy"],
             trigger_paths={"node": ["runner/src/system_actors", "types/src/constants"],
                            "cowboy": ["docs/whitepaper", "docs/cips"]},
             verify_invariants=["contract.sys_actor_address_uniqueness"]),
]

_CONTRACT_BY_ID = {c.id: c for c in CONTRACTS}

_CONTRACT_INVARIANTS = {
    "contract.tx_encoding_roundtrip": InvariantDef(
        id="contract.tx_encoding_roundtrip", domain="cross-repo", spec_ref="WP",
        executor_kind="conformance-vector", location_repo="node",
        location_path="types/src/execution.rs",
        location_test="execution::tests::test_transaction_codec_roundtrip",
        severity="high",
        run_command=["cargo", "test", "-p", "cowboy-types",
                     "execution::tests::test_transaction_codec_roundtrip", "--", "--exact"]),
    # Opcode-uniqueness guard — grown via the Marshal ratchet (escape
    # esc-20260608-cip12-cip16-opcode-collision, the 3rd opcode collision after
    # COW-1293 101<->SYS_RAS and CIP-12<->CIP-16 103-107). Two-CIP collisions
    # encode to the same byte and shadow each other's `Read for Instruction`
    # decode arm, making the older CIP's instructions undecodable on-chain =
    # consensus break. The permanent fix is `#[deny(unreachable_patterns)]` on
    # `impl Read for Instruction` (compile-time, zero-maintenance); this runtime
    # test (`sys_opcode_uniqueness`) is the surfaced double-check, run whenever a
    # PR touches the instruction encoding (tx-encoding contract, `types/src/execution`).
    # Activated when node PR #633 (deny attr + test) merged to devnet (tip
    # 6867b722) — verified `1 passed` on devnet before flipping pending=False.
    "contract.sys_opcode_uniqueness": InvariantDef(
        id="contract.sys_opcode_uniqueness", domain="cross-repo", spec_ref="WP",
        executor_kind="test", location_repo="node",
        location_path="types/src/execution.rs",
        location_test="execution::tests::sys_opcode_uniqueness",
        severity="high",
        run_command=["cargo", "test", "-p", "cowboy-types",
                     "execution::tests::sys_opcode_uniqueness", "--", "--exact"]),
    # Address-uniqueness guard — grown via escape esc-20260629-sys-addr-alloc-unverified.
    # The address-space mirror of sys_opcode_uniqueness for WP §9.1. The test already
    # existed (cowboy-runner system_actors::tests::addresses_are_unique) but was
    # unregistered; cowboy#211's 0x14 INTENT_SETTLEMENT allocation + opcode 146-151
    # reservation were verified entirely by hand (fetched cowboy-protocol-codec,
    # enumerated 130 SYS_* constants). KNOWN LIMITATION (hardening follow-up): the
    # test asserts uniqueness over a HARDCODED address array — an implementer adding a
    # new actor (e.g. INTENT_SETTLEMENT=0x14) MUST add it to that array for the guard
    # to bite. Follow-ups tracked in COW-2399: make the array exhaustive (reflect over
    # all *_SYSTEM_ACTOR consts), harden system_actor_addrmap.py (spec<->code
    # reconciliation) from xfail skeleton to a hard gate, and add an opcode spec<->codec
    # reconciliation analog. Verified `1 passed` on node main before registering.
    "contract.sys_actor_address_uniqueness": InvariantDef(
        id="contract.sys_actor_address_uniqueness", domain="cross-repo",
        spec_ref="WP", executor_kind="test", location_repo="node",
        location_path="runner/src/system_actors.rs",
        location_test="system_actors::tests::addresses_are_unique",
        severity="high",
        run_command=["cargo", "test", "-p", "cowboy-runner", "--lib",
                     "system_actors::tests::addresses_are_unique", "--", "--exact"]),
    "contract.runner_types_serde": InvariantDef(
        id="contract.runner_types_serde", domain="cross-repo", spec_ref="CIP-2",
        executor_kind="conformance-vector", location_repo="node",
        location_path="runner/src/types.rs",
        location_test="types::tests::spec_c1_7_runner_result_signature_serde_roundtrip",
        severity="high",
        run_command=["cargo", "test", "-p", "cowboy-runner",
                     "types::tests::spec_c1_7_runner_result_signature_serde_roundtrip",
                     "--", "--exact"]),
    # 真实锚点: node/ras/src/test_vectors.rs 的锁定金标准哈希向量 (已验证存在且绿)。
    # 顶层 #[test] (mod test_vectors), 用子串过滤, 不加 --exact。
    "contract.ras_canonical_vectors": InvariantDef(
        id="contract.ras_canonical_vectors", domain="cross-repo", spec_ref="CIP-9",
        executor_kind="conformance-vector", location_repo="node",
        location_path="ras/src/test_vectors.rs",
        location_test="test_vectors::locked_canonical_hashes_match_expected",
        severity="high",
        run_command=["cargo", "test", "-p", "cowboy-ras",
                     "locked_canonical_hashes_match_expected"]),
    # 真实锚点: node/types/src/cbss.rs 的 CBSS 编码往返族, 以 release_request_body
    # (CIP-24 释放请求核心线类型) 为代表 (已验证存在且绿)。mod tests, 用 module-path
    # 子串过滤。
    "contract.cbss_wire_round_trip": InvariantDef(
        id="contract.cbss_wire_round_trip", domain="cross-repo", spec_ref="CIP-24",
        executor_kind="conformance-vector", location_repo="node",
        location_path="types/src/cbss.rs",
        location_test="cbss::tests::release_request_body_round_trip",
        severity="high",
        run_command=["cargo", "test", "-p", "cowboy-types",
                     "cbss::tests::release_request_body_round_trip"]),
}

# State/consensus invariant family — harvested from the knowledge core into the
# version-controlled pack (架构 §4.6(4) DB⇄包晋升回路). Grown via the Marshal
# ratchet (escape esc-20260601-01) while reviewing the CIP-4 state-sync PR.
# Surfaced when a node change touches the state/consensus surface (_STATE_PREFIXES).
# NOTE: these live in a module-nested test, so run_command uses a substring
# filter (NOT `-- --exact`, which won't match `state_invariants::tests::…`).
_STATE_PREFIXES = ("storage/", "chain/")

_STATE_INVARIANTS = [
    InvariantDef(id="state.root_consistent_propose_verify_report", domain="state-consensus",
                 spec_ref="CIP-4", executor_kind="test", location_repo="node",
                 location_path="storage/src/state_invariants.rs",
                 location_test="prop_merkle_root_consistent_across_phases", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-storage",
                              "prop_merkle_root_consistent_across_phases"]),
    InvariantDef(id="state.speculative_rollback_equivalent", domain="state-consensus",
                 spec_ref="CIP-4", executor_kind="test", location_repo="node",
                 location_path="storage/src/state_invariants.rs",
                 location_test="prop_speculative_rollback_equivalent", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-storage",
                              "prop_speculative_rollback_equivalent"]),
    InvariantDef(id="state.root_reflects_committed_set", domain="state-consensus",
                 spec_ref="CIP-4", executor_kind="test", location_repo="node",
                 location_path="storage/src/state_invariants.rs",
                 location_test="prop_root_reflects_committed_set", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-storage",
                              "prop_root_reflects_committed_set"]),
    # Ratchet esc-20260605-timer-wallclock (PR #583 / COW-1272): committed timer
    # set + state root must be independent of wall-clock execution speed (the
    # inline dispatch loop skips timers on an Instant::now() budget → speed-
    # dependent root → consensus split). Registered as an #[ignore]d skeleton in
    # node; pending=True until the injectable-budget seam lands with the COW-1272
    # fix and the two-run root-equality body replaces the panic. Flip pending to
    # False then. Kept out of gate dispatch + conformance while pending.
    InvariantDef(id="state.timer_dispatch_wall_clock_independent", domain="state-consensus",
                 spec_ref="CIP-4", executor_kind="test", location_repo="node",
                 location_path="storage/src/state_invariants.rs",
                 location_test="state_invariants::tests::prop_timer_dispatch_wall_clock_independent",
                 severity="high", pending=True,
                 run_command=["cargo", "test", "-p", "cowboy-storage",
                              "prop_timer_dispatch_wall_clock_independent"]),
]


# CIP-7 stream invariant family. Grown via the Marshal ratchet (escape
# esc-20260608-cip7-stream-decrypt) reviewing node PR #623: an actor-level
# stream_decrypt gated *ciphertext retrieval* behind a local sender-keyed
# entitlement cache, contradicting CIP-7 — ciphertext retrieval MUST NOT be
# entitlement-gated; only the decryption key is, on the SKM (§542-543/§772-773).
# The guard pins that ciphertext stays retrievable (get_since/get_latest) for an
# un-entitled reader. Real, merged, verified-passing test in node devnet.
# Surfaced when a change touches the CIP-7 stream surface (_CIP7_PREFIXES).
_CIP7_PREFIXES = ("cli/actors/stream_actor.py", "cli/actors/test_stream_actor.py",
                  "pvm/Lib/cowboy_sdk/stream.py")
_CIP7_INVARIANTS = [
    InvariantDef(id="cip7.ciphertext_access_not_entitlement_gated", domain="cip7",
                 spec_ref="CIP-7", executor_kind="test", location_repo="node",
                 location_path="cli/actors/test_stream_actor.py",
                 location_test="test_cip7_ciphertext_access_not_entitlement_gated",
                 severity="medium",
                 run_command=["python3", "-m", "pytest", "cli/actors/test_stream_actor.py",
                              "-k", "ciphertext_access_not_entitlement_gated"]),
]


# CIP-14 HTTP-ingress invariant family — backfilled from the Marshal conformance
# gap (CIP-14 was 0-of-39-MUST covered). CIP-14 is otherwise spec-ahead-of-code:
# the Gateway / query path / `http.request` handler / `target_pool` enum are not
# implemented yet, so the only req with real, pure, round-trippable code is
# §8.6 (req-26) — the reserved `/_cowboy/` namespace MUST NOT be overridable by a
# route manifest, enforced in `ras/src/route_manifest.rs::check_path_prefix`.
# Surfaced when a change touches the route-manifest surface (_RAS_ROUTE_PREFIXES).
_RAS_ROUTE_PREFIXES = ("ras/src/route_manifest",)

_RAS_ROUTE_INVARIANTS = [
    InvariantDef(id="contract.cip14_reserved_path_prefix", domain="cross-repo",
                 spec_ref="CIP-14", executor_kind="proptest", location_repo="node",
                 location_path="ras/src/route_manifest.rs",
                 location_test="route_manifest::tests::prop_reserved_path_prefix_always_rejected",
                 severity="mid",
                 run_command=["cargo", "test", "-p", "cowboy-ras",
                              "prop_reserved_path_prefix_always_rejected"]),
]


# PVM strict-determinism invariant family — grown via the Marshal ratchet
# (escape esc-20260605-pvm-ci-gap) while reviewing node PR #609 (COW-366 local
# `--strict` determinism). The escape: the `--strict` happy path was broken
# (valid actor code rejected with InvalidInput — a continuation SyntaxError
# raised while full enforcement loaded the preamble's stdlib), yet CI was green
# twice because `pvm/` is a workspace-excluded SEPARATE Cargo workspace that
# `cargo test --workspace` from node/ never runs. The complementary product fix
# is to wire the pvm/ workspace into node CI; this invariant makes the Marshal
# gate run the happy-path test on every pvm change regardless.
# NOTE: run_command uses `--manifest-path pvm/Cargo.toml` because pvm/ is a
# separate workspace; substring filter (not `-- --exact`) hits the integration
# test in tests/regression/simulate.rs via the `lib` test target.
_PVM_PREFIXES = ("pvm/crates/pvm-runtime/src/simulate",
                 "pvm/crates/pvm-runtime/src/determinism",
                 "pvm/crates/pvm-runtime/src/lib",
                 # reflection guard surfaces on the runtime builtins/scope path and
                 # on the node-side deploy validator that owns validate_actor_code.
                 "pvm/crates/vm/src/scope",
                 # the cold-stdlib mis-rejection lives in the FSM continuation
                 # codegen pass, which compiles every module on the determinism path.
                 "pvm/crates/codegen/src/pvm_fsm",
                 "execution/src/pvm_executor")

_PVM_INVARIANTS = [
    InvariantDef(id="pvm.strict_simulation_allows_valid_code", domain="determinism",
                 spec_ref="COW-366", executor_kind="test", location_repo="node",
                 location_path="pvm/crates/pvm-runtime/tests/regression/simulate.rs",
                 location_test="regression::simulate::run_simulation_strict_allows_deterministic_code",
                 severity="high",
                 run_command=["cargo", "test", "--manifest-path", "pvm/Cargo.toml",
                              "-p", "pvm-runtime", "--test", "lib",
                              "run_simulation_strict_allows_deterministic_code"]),
    # (escape esc-20260610-pvm-reflection-bypass) from node PR #660 (COW-2051,
    # WP §3 reflection block). The PR's validate_actor_code only rejects a Call
    # whose callee is a *bare Name* eval/exec/compile. Indirect reflection —
    # `e = eval; e(...)`, `getattr(__builtins__,"eval")(...)`, `__builtins__["exec"](...)`
    # — reaches the unstripped runtime builtins (Scope::with_builtins injects the
    # full vm.builtins; INT_GUARD_PREAMBLE only replaces `int`), so WP §3's
    # reflection prohibition is still violable at runtime. Almanax flagged HIGH;
    # Marshal independently found the same vectors but issued a clean PASS and
    # mis-reported Almanax as 0 findings (4 min after the HIGH went live). Static
    # AST analysis CANNOT catch the aliased/subscript forms without full scope
    # analysis — the real fix is a runtime builtins guard (del/guard eval/exec/
    # compile in the actor scope while routing stdlib internals through a
    # privileged loader). pending=True: the runtime guard and this regression test
    # are not yet written; flip to active once the guard lands.
    InvariantDef(id="pvm.reflection_indirect_bypass_blocked", domain="determinism",
                 spec_ref="WP-§3", executor_kind="test", location_repo="node",
                 location_path="pvm/crates/pvm-runtime/tests/regression/reflection.rs",
                 location_test="regression::reflection::indirect_eval_bypass_is_neutralized",
                 severity="high", pending=True,
                 run_command=["cargo", "test", "--manifest-path", "pvm/Cargo.toml",
                              "-p", "pvm-runtime", "--test", "lib",
                              "indirect_eval_bypass_is_neutralized"]),
    # (escape esc-20260610-pvm-fsm-stdlib-cold-misreject) found re-reviewing node
    # PR #665: pvm_fsm.rs::continuation_meta rejects ANY decorated (async) function
    # whose decorator is not @runner/actor.continuation, and stdlib
    # _collections_abc.py has `@abstractmethod async def __anext__/asend/athrow` —
    # so a COLD interpreter compiling the whitelisted stdlib chain under the
    # determinism path (preamble `import re` → enum → functools → collections →
    # _collections_abc) dies with SyntaxError. Warm-pool sys.modules caching masks
    # it: the full pvm-runtime suite is green while filtered/standalone runs of the
    # same tests FAIL (order-dependent false-green); pre-existing on devnet
    # (decimal_default_context_is_deterministic fails standalone at base 2a286711),
    # and CI never sees it because pvm/ is workspace-excluded (COW-366 family).
    # The run_command is a single-test filtered invocation ON PURPOSE: a fresh
    # process = cold interpreter pool, which is exactly the property under test —
    # do NOT "fix" it to run inside the full suite. pending=True: the codegen fix
    # (skip/ignore non-continuation decorators on non-actor compiles) and this
    # regression test are not yet written; flip to active once they land.
    InvariantDef(id="pvm.cold_determinism_stdlib_import_allowed", domain="determinism",
                 spec_ref="WP-§3", executor_kind="test", location_repo="node",
                 location_path="pvm/crates/pvm-runtime/tests/regression/determinism_hardening.rs",
                 location_test="regression::determinism_hardening::cold_interpreter_compiles_whitelisted_stdlib_under_determinism",
                 severity="high", pending=True,
                 run_command=["cargo", "test", "--manifest-path", "pvm/Cargo.toml",
                              "-p", "pvm-runtime", "--test", "lib",
                              "regression::determinism_hardening::cold_interpreter_compiles_whitelisted_stdlib_under_determinism",
                              "--", "--exact"]),
]


# CBSS IBE crypto invariant family — harvested from the knowledge core into the
# version-controlled pack (架构 §4.6(4) DB⇄包晋升回路). Grown via the Marshal
# ratchet (escape cbss-crypto-no-invariant) while reviewing the cowboy-crypto SDK
# packaging PR (node #470): the cbss-crypto crate had zero invariant coverage —
# only a single fixed-vector golden test. Surfaced when a change touches the
# crypto crate or its Python bindings (_CRYPTO_PREFIXES).
# NOTE: the IBE round-trip test lives in the **cbss repo** (crates/cbss-crypto),
# NOT node — the node-packaged cbss-crypto crate (PR #470) was never merged to
# node devnet, so the earlier node-pointing reference was a phantom. Real,
# verified anchor: cbss `ibe::tests::ibe_round_trip_matches_bilinearity`.
# Surfaced on either the node-packaged paths (cbss-crypto/, cowboy-py/) or the
# cbss-repo paths (handled by _CBSS_PREFIXES / _CBSS_INVARIANTS below).
_CRYPTO_PREFIXES = ("cbss-crypto/", "cowboy-py/")

_CRYPTO_INVARIANTS = [
    InvariantDef(id="crypto.cbss_ibe_roundtrip", domain="crypto", spec_ref="CIP-24",
                 executor_kind="proptest", location_repo="cbss",
                 location_path="crates/cbss-crypto/src/ibe.rs",
                 location_test="ibe::tests::ibe_round_trip_matches_bilinearity", severity="high",
                 run_command=["cargo", "test", "-p", "cbss-crypto",
                              "ibe::tests::ibe_round_trip_matches_bilinearity", "--", "--exact"]),
]


# cbfs 领域不变量 (住 cbfs 仓)。CIP-9 耐久性核心: erasure 编码后任意 K/(K+M) 分片
# 可精确重建。真实 proptest, 已验证通过 (proptest sweep, 模块内嵌 → 子串过滤)。
# 碰 erasure/ 时触发。
_CBFS_PREFIXES = ("erasure/",)
_CBFS_INVARIANTS = [
    InvariantDef(id="cbfs.erasure_any_k_reconstructs", domain="storage", spec_ref="CIP-9",
                 executor_kind="proptest", location_repo="cbfs",
                 location_path="erasure/src/lib.rs",
                 location_test="tests::prop_any_k_subset_reconstructs", severity="high",
                 run_command=["cargo", "test", "-p", "cbfs-erasure",
                              "prop_any_k_subset_reconstructs"]),
]

# cbss 领域不变量 (住 cbss 仓)。CIP-24 释放正确性核心: 任意 t-quorum 的部分签名
# Lagrange 组合恢复同一 identity*secret, 任意 <t 失败。真实 proptest, 已验证通过。
# 碰 crates/cbss-crypto/ 时触发。
_CBSS_PREFIXES = ("crates/cbss-crypto/",)
_CBSS_INVARIANTS = [
    InvariantDef(id="cbss.threshold_any_t_recovers", domain="crypto", spec_ref="CIP-24",
                 executor_kind="proptest", location_repo="cbss",
                 location_path="crates/cbss-crypto/src/threshold.rs",
                 location_test="threshold::tests::prop_any_t_subset_recovers", severity="high",
                 run_command=["cargo", "test", "-p", "cbss-crypto",
                              "prop_any_t_subset_recovers"]),
]

# cbqs 领域不变量 (住 cbqs 仓)。CIP-39 交付正确性核心 (at-least-once): 一个非终态的
# 租约过期**不得**把消费组 safe-prefix 推过仍可重投的记录 —— 否则 safe prefix 越过一条
# 未确认交付的消息 = 静默丢消息。真实单测, 已在 cbqs head 验证通过 (running 1 test; ok)。
# 碰交付/终态/游标逻辑 (standard_delivery/standard_group/standard_lane) 时触发。
_CBQS_PREFIXES = ("crates/cbqsd/src/standard_delivery",
                  "crates/cbqsd/src/standard_group",
                  "crates/cbqsd/src/standard_lane")
_CBQS_INVARIANTS = [
    InvariantDef(id="cbqs.at_least_once_safe_prefix_holds", domain="messaging", spec_ref="CIP-39",
                 executor_kind="test", location_repo="cbqs",
                 location_path="crates/cbqsd/src/standard_delivery.rs",
                 location_test=("standard_delivery::tests::"
                                "nonterminal_lease_expiry_does_not_advance_past_the_redeliverable_record"),
                 severity="high",
                 run_command=["cargo", "test", "-p", "cbqsd", "--lib",
                              "nonterminal_lease_expiry_does_not_advance_past_the_redeliverable_record"]),
]

# cowboy-protocol 领域不变量 (住 cowboy-protocol 仓)。共识核心: 交易的 signing-preimage /
# signing-hash / 提交 wire 编码与 canyon node 输出**字节相同** (COW-2360 hard gate)。任何 drift
# = 两实现算出不同 tx hash / 接受不同字节 = 硬分叉。真实 golden-vector 测试, 已在 head 验证通过。
# 注意: 整档 `#![cfg(feature="signing")]`, run_command **必须**带 `--features signing`,否则跑 0 test
# 假绿 (执行器的 ≥1-test-ran 检查会把 0-test 当 degraded 暴露, 非误 PASS)。碰 codec 时触发。
_PROTOCOL_PREFIXES = ("crates/cowboy-protocol-codec/",)
_PROTOCOL_INVARIANTS = [
    InvariantDef(id="protocol.tx_canonical_bytes_identical_to_node", domain="consensus", spec_ref="WP",
                 executor_kind="conformance-vector", location_repo="cowboy-protocol",
                 location_path="crates/cowboy-protocol-codec/tests/golden_vectors.rs",
                 location_test="golden_vectors_byte_identity", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-protocol-codec",
                              "--features", "signing", "--test", "golden_vectors",
                              "golden_vectors_byte_identity"]),
]

# gateway 领域不变量 (住 gateway 仓)。x402 支付安全核心: serve-before-settle 双花守卫 —— 同一
# 支付凭证 (nonce key) 一旦 in-flight 被 claim, 第二个并发同凭证请求必须被拒 (不同 key 仍可成功),
# 否则同一签名支付可在链上 nonce 消费前被服务/结算两次 = 直接资金损失。确定性单测 (无 sleep/网络),
# 已在 head 验证通过。碰 x402 支付面时触发。
_GATEWAY_PREFIXES = ("crates/gateway-x402/",)
_GATEWAY_INVARIANTS = [
    InvariantDef(id="gateway.x402_no_double_serve_before_settle", domain="payments", spec_ref="x402",
                 executor_kind="test", location_repo="gateway",
                 location_path="crates/gateway-x402/src/lib.rs",
                 location_test="tests::reservation_blocks_concurrent_same_key", severity="high",
                 run_command=["cargo", "test", "-p", "gateway-x402",
                              "tests::reservation_blocks_concurrent_same_key"]),
]

# runner 领域不变量 (住 runner 仓)。off-chain 结果验证安全核心: N-of-M MajorityVote 在同意门槛
# 未达 (3 中仅 2 一致而要求全体) 时**必须**拒绝认证 (返回 ThresholdNotMet), 否则缺乏所需共识的
# 错误 off-chain 结果会被当 canonical 结算上链。确定性 (纯内存, 无网络/docker/TEE), 已验证通过。
# 碰 result-verifier 时触发。
_RUNNER_PREFIXES = ("crates/result-verifier/",)
_RUNNER_INVARIANTS = [
    InvariantDef(id="runner.majority_vote_rejects_below_threshold", domain="verification", spec_ref="CIP-2",
                 executor_kind="test", location_repo="runner",
                 location_path="crates/result-verifier/src/verifier.rs",
                 location_test="verifier::tests::test_majority_vote_threshold_not_met_fails", severity="high",
                 run_command=["cargo", "test", "-p", "result-verifier",
                              "verifier::tests::test_majority_vote_threshold_not_met_fails"]),
]

# 阶段二 (review-hazard) 不变量:JS/TS 仓 (wallet, store-admin) 没有 cargo/pytest 可跑的机械锚,
# 只能由对抗 review 裁定 (invariant_able=False)。这些**不进** list_invariants (否则机械门禁会去
# 跑一条无 run_command 的检查 → 假 degraded);它们的「牙」在上面 SECURITY_HAZARDS 的同源条目
# (security_hazards() 按 when_paths 触发, 把 prompt 注入 review 并升 high)。此处只登记为 registry
# 记录 (进 all_invariant_defs → 供 reconcile seed, 让 dashboard 如实显示覆盖)。location_test 留空。
_REVIEW_INVARIANTS = [
    InvariantDef(id="wallet.no_blind_signing_intent_binding", domain="security", spec_ref="",
                 executor_kind="review-hazard", location_repo="wallet",
                 location_path="src/lib/sign-binding.js", location_test="", severity="high"),
    InvariantDef(id="store-admin.privileged_op_server_authz", domain="security", spec_ref="",
                 executor_kind="review-hazard", location_repo="store-admin",
                 location_path="src/services/approval-orchestrator.ts", location_test="", severity="high"),
]


# 安全信任边 / 危险点 (架构: 否定性·对抗性属性,不可往返化)。
# 教训源 = almanax 在 node #470 标出的 Critical:`cbss_encrypt_secret` 把 wrap key
# 派生成 `pairing(H1(aad), mpk_g2)`,无 per-message 随机数,而 `mpk_g2` 经 RPC
# (`/cbss/account-release-key/...`) 公开 —— 任何观察者可复算 wrap key 解密,机密性破裂。
# 关键认知:机密性 / IND-CPA 是**否定性属性**("除持密钥者外无人能恢复明文")。
# 功能往返不变量 `crypto.cbss_ibe_roundtrip`(decrypt(encrypt(m))==m)在脆弱构造上
# 照样为绿,结构上表达不了它。所以棘轮**不能**用一条 roundtrip proptest 来"补"这类洞;
# 它只能作为 review lens 的危险点暴露 (invariant_able=False)。同时它需要跨 crate 的
# 信任模型知识 (mpk 经 RPC 公开),不在 diff 内 —— 故显式建模成一条 trust 边。
@dataclass
class SecurityHazard:
    id: str
    when_paths: tuple[str, ...]      # 命中这些 diff 前缀即触发
    title: str
    prompt: str                       # 喂给 security review lens 的对抗式提示
    invariant_able: bool              # False => 不可固化为往返不变量,只能 review 裁定


SECURITY_HAZARDS = [
    SecurityHazard(
        id="cbss-mpk-rpc-exposure",
        # node 仓打包的 crate (cbss-crypto/, cowboy-py/, cowboy-crypto/) + cbss 仓
        # 真实路径: crates/cbss-crypto/ 全体, 以及 cbssd 中真正做密钥派生/封缄/份额的
        # 文件 (不含 config/transport 等普通守护代码, 否则误报)。
        # cowboy-crypto/ 是 PR #672 新增的 PyO3 镜像 kernel — 当时不在此列表导致
        # lens 没注入 (esc-20260611-cbss-ibe-no-ephemeral): kernel 复制到新路径时
        # hazard 触发器必须跟着走。
        when_paths=("cbss-crypto/", "cowboy-py/", "cowboy-crypto/",
                    "crates/cbss-crypto/",
                    "crates/cbssd/src/cip7_content_key_seal",
                    "crates/cbssd/src/cip9_volume_seal",
                    "crates/cbssd/src/hpke_identity",
                    "crates/cbssd/src/keychain",
                    "crates/cbssd/src/share_storage",
                    "crates/cbssd/src/partial_sign"),
        title="confidentiality of key derivation against a publicly exposed master key",
        prompt=("默认怀疑机密性而非功能性。CBSS master public key (mpk_g2) 经 RPC 公开 "
                "(/cbss/account-release-key/...)。检查任何 DEK / wrap-key 派生是否只用了"
                "公开量且无 per-message 随机数 —— 若是,任何观察者可复算密钥解密,机密性"
                "破裂 (IND-CPA 失败)。这是否定性属性:roundtrip 不变量无法表达,必须在此 "
                "review 中裁定 (参 node #470 almanax Critical)。"),
        invariant_able=False),
    SecurityHazard(
        id="wallet-blind-signing-intent-binding",
        # wallet 是 JS,机械执行器跑不了 —— 只能阶段二裁定。签名摘要绑定 (sign-binding.js)、
        # 审批 UI (popup/approve.js)、window.cowboy provider (content/inject, service-worker)。
        when_paths=("src/lib/sign-binding", "src/popup/approve", "src/popup/popup",
                    "src/background/service-worker", "src/content/inject",
                    "src/content/content-script"),
        title="wallet blind-signing: the signature the user approves must be bound to the intent shown",
        prompt=("默认怀疑钓鱼/盲签。任何经 window.cowboy 发起的签名请求:(1) 被签的摘要必须"
                "**域分离** —— personal-message 签名绝不能被重放为一笔交易 (transfer/deploy/"
                "execute),反之亦然;(2) 弹窗必须把真实解码后的交易/消息展示给用户,绝不签"
                "用户看不懂的 opaque bytes;(3) dApp 不得诱导钱包对任意 32 字节 hash 盲签。"
                "这是否定性属性 (盲签 oracle / 意图错绑),roundtrip 不变量表达不了,必须"
                "review 裁定 (参 esc-20260616-wallet-blind-signmessage)。"),
        invariant_able=False),
    SecurityHazard(
        id="store-admin-privileged-op-authz-and-db-boundary",
        # store-admin 是 TS,机械执行器跑不了 —— 只能阶段二裁定。特权审批/签名/发布 +
        # read-cap 校验 + README 明定「不得写 explorer chain-truth DB」的边界。
        when_paths=("src/services/signing", "src/services/read-cap-verifier",
                    "src/services/approval-orchestrator", "src/services/review-workflow",
                    "src/services/republish", "src/services/labs-publisher",
                    "src/server", "src/app"),
        title="store-admin privileged review/approval/signing must be server-authorized and stay in its DB boundary",
        prompt=("默认怀疑越权/边界破坏。检查:(1) approval / publish / sign / republish 等特权"
                "操作是否在**服务端**校验调用者的 reviewer 授权 —— 不能只靠 UI/客户端门控,"
                "不能有 submission/member id 的 IDOR;(2) read-cap-verifier 是否真的门住访问,"
                "不被绕过;(3) store-admin 是否**从不写 explorer chain-truth DB** (README 硬边界),"
                "签名适配器不得越权铸造链上真相。否定性属性 (broken access control / 边界越界),"
                "roundtrip 表达不了,必须 review 裁定。"),
        invariant_able=False),
]


# 分层规格体系 (架构 §4.5): 白皮书=宪法, CIP=修正案. 源在 cowboy 仓库.
# 权威源 (用户确认): https://github.com/cowboyinc/cowboy/tree/main/docs/{cips,whitepaper}
SPEC_LAYERS = [
    {"id": "whitepaper", "role": "constitution", "authority": "root",
     "repo": "cowboy", "source": "docs/whitepaper",
     "main": "cowboy-technical-whitepaper.md"},
    {"id": "cip", "role": "amendment", "authority": "amends-constitution",
     "repo": "cowboy", "source": "docs/cips"},
]
PRECEDENCE_NORMATIVE = ["whitepaper", "cip"]            # 应然: CIP 在触及处覆盖白皮书
PRECEDENCE_DESCRIPTIVE = ["whitepaper", "cip", "code"]  # 实然: 代码为锚

_CIP_RE = re.compile(r"^CIP-(\d+)$")

# RFC2119 normative keywords, longest-first so "MUST NOT" wins over "MUST".
_RFC2119 = [
    ("MUST NOT", "must"), ("SHALL NOT", "must"), ("MUST", "must"),
    ("SHALL", "must"), ("REQUIRED", "must"),
    ("SHOULD NOT", "should"), ("SHOULD", "should"), ("RECOMMENDED", "should"),
    ("MAY", "may"), ("OPTIONAL", "may"),
]
_RFC2119_RE = [(kw, level, re.compile(r"\b" + kw.replace(" ", r"\s+") + r"\b"))
               for kw, level in _RFC2119]


class CowboyPack:
    @property
    def id(self) -> str:
        return "cowboy"

    def spec_layers(self) -> list[dict]:
        """分层规格体系声明 (供 ⑤/③ 解析规格层与定位源)。"""
        return [dict(layer) for layer in SPEC_LAYERS]

    def resolve_spec_ref(self, spec_ref: str) -> dict | None:
        """把一个 spec_ref 标签解析到其正文源位置 (repo + path_glob)。

        `CIP-<n>` → cowboy 仓库 docs/cips/cip-<n>-*.md;`WP`/`WHITEPAPER` →
        技术白皮书主文件。非规格标签 (如 C-1 / M-B / CIP-?) 返回 None —— 它们
        不是 CIP/白皮书条款,没有可读正文源。调用方据此 JIT 读取规格正文。
        """
        if not spec_ref:
            return None
        ref = spec_ref.strip()
        m = _CIP_RE.match(ref)
        if m:
            return {"layer": "cip", "repo": "cowboy",
                    "path_glob": f"docs/cips/cip-{int(m.group(1))}-*.md"}
        if ref.upper() in ("WP", "WHITEPAPER"):
            return {"layer": "whitepaper", "repo": "cowboy",
                    "path_glob": "docs/whitepaper/cowboy-technical-whitepaper.md"}
        return None

    def list_invariants(self, scope: dict) -> list[InvariantDef]:
        out = []
        paths = scope.get("diff_paths", [])
        if scope.get("repo") == "node":
            out.extend(_ECON_INVARIANTS)
            if any(p.startswith(_STATE_PREFIXES) for p in paths):
                out.extend(_STATE_INVARIANTS)
            if any(p.startswith(_CRYPTO_PREFIXES) for p in paths):
                out.extend(_CRYPTO_INVARIANTS)
            if any(p.startswith(_PVM_PREFIXES) for p in paths):
                out.extend(_PVM_INVARIANTS)
            if any(p.startswith(_CIP7_PREFIXES) for p in paths):
                out.extend(_CIP7_INVARIANTS)
            if any(p.startswith(_RAS_ROUTE_PREFIXES) for p in paths):
                out.extend(_RAS_ROUTE_INVARIANTS)
        elif scope.get("repo") == "cbfs":
            if any(p.startswith(_CBFS_PREFIXES) for p in paths):
                out.extend(_CBFS_INVARIANTS)
        elif scope.get("repo") == "cbss":
            if any(p.startswith(_CBSS_PREFIXES) for p in paths):
                out.extend(_CBSS_INVARIANTS)
        elif scope.get("repo") == "cbqs":
            if any(p.startswith(_CBQS_PREFIXES) for p in paths):
                out.extend(_CBQS_INVARIANTS)
        elif scope.get("repo") == "cowboy-protocol":
            if any(p.startswith(_PROTOCOL_PREFIXES) for p in paths):
                out.extend(_PROTOCOL_INVARIANTS)
        elif scope.get("repo") == "gateway":
            if any(p.startswith(_GATEWAY_PREFIXES) for p in paths):
                out.extend(_GATEWAY_INVARIANTS)
        elif scope.get("repo") == "runner":
            if any(p.startswith(_RUNNER_PREFIXES) for p in paths):
                out.extend(_RUNNER_INVARIANTS)
        seen = {i.id for i in out}
        for cid in self.contracts_hit(scope):
            for inv_id in _CONTRACT_BY_ID[cid].verify_invariants:
                inv = _CONTRACT_INVARIANTS.get(inv_id)
                if inv and inv.id not in seen:
                    out.append(inv)
                    seen.add(inv.id)
        # pending invariants are registered/tracked but not yet enforcing
        # (#[ignore]d skeletons); never hand them to the gate runner — a 0-passed
        # ignored test would read as a false `degraded`. They activate when their
        # body lands and `pending` is flipped to False.
        return [i for i in out if not i.pending]

    def all_invariant_defs(self) -> list[InvariantDef]:
        """The **complete** catalog across every repo, independent of any scope —
        the source of truth `list_invariants` samples from on demand. Used by the
        reconcile path to seed the DB registry with catalog invariants that no PR
        has happened to exercise yet (so a newly-onboarded repo reads its real
        coverage instead of 0). Deduped by id, first occurrence wins. `pending`
        skeletons are included here (the caller decides to skip them) so the full
        catalog stays inspectable."""
        out: list[InvariantDef] = []
        seen: set[str] = set()
        for coll in (_ECON_INVARIANTS, list(_CONTRACT_INVARIANTS.values()),
                     _STATE_INVARIANTS, _CIP7_INVARIANTS, _RAS_ROUTE_INVARIANTS,
                     _PVM_INVARIANTS, _CRYPTO_INVARIANTS, _CBFS_INVARIANTS,
                     _CBSS_INVARIANTS, _CBQS_INVARIANTS, _PROTOCOL_INVARIANTS,
                     _GATEWAY_INVARIANTS, _RUNNER_INVARIANTS, _REVIEW_INVARIANTS):
            for inv in coll:
                if inv.id not in seen:
                    out.append(inv)
                    seen.add(inv.id)
        return out

    def classify(self, scope: dict) -> str:
        return self.classify_detailed(scope)["tier"]

    def classify_detailed(self, scope: dict) -> dict:
        paths = scope.get("diff_paths", [])
        text = scope.get("diff_text", "")
        reasons = []

        contracts = self.contracts_hit(scope)
        for cid in contracts:
            reasons.append(f"cross_repo_contract:{cid}")

        if any(p.startswith(_HIGH_PREFIXES) for p in paths):
            reasons.append("high-risk path (execution/storage/chain consensus)")
        repo_high = _REPO_HIGH_PREFIXES.get(scope.get("repo", ""))
        if repo_high and any(p.startswith(repo_high[0]) for p in paths):
            reasons.append(repo_high[1])
        if any(s in p for p in paths for s in _HIGH_SUBSTR):
            reasons.append("crypto / *_root computation")
        for hz in self.security_hazards(scope):
            reasons.append(f"security-hazard:{hz['id']} (review-only)")
        if any(t in text for t in _SYS_ADDR_TOKENS):
            reasons.append("system actor address logic")
        if any(lbl in ("cip:new", "cip:interface-change")
               for lbl in scope.get("labels", [])):
            reasons.append("CIP new / interface change")

        ci_paths = [p for p in paths if p.startswith(_CI_PREFIX)]

        if contracts or reasons:
            tier = "high"
        elif paths and all(p.endswith(_LOW_SUFFIXES) or any(s in p for s in _LOW_SUBSTR)
                           or p.startswith(_CI_PREFIX) for p in paths):
            tier = "low"
            if ci_paths:
                reasons.append("CI/infra workflow (operational, non-privileged)")
        else:
            tier = "mid"
            reasons.append("default mid (ordinary actor / RPC handler)")

        return {"tier": tier, "reasons": reasons, "contracts_hit": contracts,
                "security_hazards": self.security_hazards(scope),
                "review_dimensions": [d["name"]
                                      for d in self.review_plan({**scope, "tier": tier})]}

    def security_hazards(self, scope: dict) -> list[dict]:
        """否定性/对抗性安全危险点 (信任边)。命中即附一条 review lens 提示。

        与不变量分开:`invariant_able=False` 的危险点**不可**用功能往返 proptest 固化
        (机密性/IND-CPA 这类在脆弱构造上 roundtrip 照样为绿),只能由 security review
        裁定。skill 应把这些 prompt 注入对抗 review,且棘轮遇到这类根因不得 spawn
        roundtrip 不变量 (见 ratchet_guidance)。"""
        paths = scope.get("diff_paths", [])
        out = []
        for hz in SECURITY_HAZARDS:
            if any(p.startswith(hz.when_paths) for p in paths):
                out.append({"id": hz.id, "title": hz.title, "prompt": hz.prompt,
                            "invariant_able": hz.invariant_able})
        # CI/CD threat model: combination over whole workflow-file content (P1+P2).
        # Negative properties (invariant_able=False) — they escalate tier + inject a
        # review lens, exactly like the path-based hazards above.
        ci_hazards, _ = ci_security.scan_scope(scope)
        for hz in ci_hazards:
            out.append({"id": hz["id"], "title": hz["title"], "prompt": hz["prompt"],
                        "invariant_able": hz["invariant_able"],
                        "severity": hz["severity"], "path": hz["path"],
                        "evidence": hz["evidence"]})
        return out

    def ratchet_guidance(self, root_cause_class: str) -> dict:
        """棘轮形状守门:否定性属性 (机密性/保密/IND-CPA/泄露/越权) 不可往返化。

        遇到这些根因,spawn 一条功能 roundtrip proptest 会造出"绿着却漏"的假覆盖
        (正是 node #470 的教训:crypto.cbss_ibe_roundtrip 漏掉了 confidentiality break)。
        返回 invariant_able=False 时,permanent guard 应是一条 review-lens 危险点
        (SecurityHazard),而非 proptest。"""
        rc = (root_cause_class or "").lower()
        negative = ("confidential", "secrecy", "ind-cpa", "ind_cpa", "leak",
                    "exposure", "disclos", "auth", "privilege", "side-channel",
                    "side_channel")
        if any(k in rc for k in negative):
            return {"invariant_able": False, "preferred_shape": "review-hazard",
                    "reason": ("negative/adversarial property — a functional roundtrip "
                               "proptest is green on the vulnerable construction; spawn a "
                               "review-lens SecurityHazard, not a roundtrip invariant.")}
        return {"invariant_able": True, "preferred_shape": "proptest",
                "reason": "functional/safety property — a property test can express it."}

    def parse_spec_requirements(self, text: str) -> list[dict]:
        """⑤ parse_spec_requirements 种子: 从规格正文抽 RFC2119 规范性条款作为候选
        requirement (要求级 conformance 的分母侧)。启发式、逐行扫描:每行取其中最强的
        RFC2119 关键字 (大写才算规范性);MUST/SHALL/REQUIRED=must, SHOULD/RECOMMENDED=
        should, MAY/OPTIONAL=may。代码块/示例里的关键字可能误抽,属已知粗粒度局限。
        """
        reqs = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            for kw, level, rx in _RFC2119_RE:
                if rx.search(line):
                    reqs.append({"id": f"req-{len(reqs) + 1}", "level": level,
                                 "keyword": kw, "text": line})
                    break
        return reqs

    def conformance_matrix(self) -> dict:
        """Spec-ref → [invariant ids] coverage (⑤ ConformanceGov 矩阵种子 / ⑦
        conformance%). 只计 spec_ref 能解析到真实规格源的不变量;不可解析的标签
        (内部 tag) 不计入,以免虚报覆盖。调用方可拿全 CIP 集与之做差得未覆盖项。
        """
        out: dict = {}
        all_defs = (list(_ECON_INVARIANTS) + list(_CONTRACT_INVARIANTS.values())
                    + list(_STATE_INVARIANTS) + list(_CRYPTO_INVARIANTS)
                    + list(_CBFS_INVARIANTS) + list(_CBSS_INVARIANTS)
                    + list(_CBQS_INVARIANTS) + list(_PROTOCOL_INVARIANTS)
                    + list(_GATEWAY_INVARIANTS) + list(_RUNNER_INVARIANTS)
                    + list(_CIP7_INVARIANTS) + list(_RAS_ROUTE_INVARIANTS))
        for inv in all_defs:
            # pending (not-yet-enforcing) invariants must not inflate coverage.
            if inv.pending or self.resolve_spec_ref(inv.spec_ref) is None:
                continue
            out.setdefault(inv.spec_ref, []).append(inv.id)
        return out

    def review_plan(self, scope: dict) -> list[dict]:
        """按 scope 选对抗 review 视角 (机制: tier×路径; 数据: 本包)。

        core 只收 [{name, prompt}]。scope 带 `tier` 则直接用 (classify_detailed
        已算过, 省一次重复 classify); 缺则由 classify(scope) 推。签名吃整个 scope
        (含 diff_paths) 是为将来让视角按路径条件触发 (对齐 list_invariants /
        security_hazards); 当前实现仍按 tier 定视角数。
        """
        tier = scope.get("tier") or self.classify(scope)
        n = {"high": 6, "mid": 3, "low": 1}.get(tier, 3)
        plan = [dict(d) for d in REVIEW_DIMENSIONS[:n]]     # tier 基集(有序前缀)
        seen = {d["name"] for d in plan}
        paths = scope.get("diff_paths", [])
        for dim in PATH_REVIEW_DIMENSIONS:                  # 路径触发, 并入去重
            if dim["name"] in seen:
                continue
            if any(sub in p for p in paths for sub in dim["when_paths"]):
                plan.append({"name": dim["name"], "prompt": dim["prompt"]})
                seen.add(dim["name"])
        return plan

    def contracts_hit(self, scope: dict) -> list[str]:
        repo = scope.get("repo", "")
        paths = scope.get("diff_paths", [])
        hit = []
        for c in CONTRACTS:
            prefixes = tuple(c.trigger_paths.get(repo, []))
            if prefixes and any(p.startswith(prefixes) for p in paths):
                hit.append(c.id)
        return hit
