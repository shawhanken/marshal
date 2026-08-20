"""安全危险点 / 棘轮形状守门 — node #470 教训的回归测试。

almanax 在 #470 标的 Critical 是 confidentiality break (否定性属性);marshal 之前
用一条功能往返不变量 crypto.cbss_ibe_roundtrip "补"了覆盖空洞,而往返测试在脆弱构造上
照样为绿 —— 抓不住它。这些测试把"否定性属性不可往返化"固化进 pack。
"""
from marshal_pack_cowboy.pack import CowboyPack

PACK = CowboyPack()


def test_crypto_diff_triggers_confidentiality_hazard():
    hz = PACK.security_hazards({"repo": "node", "diff_paths": ["cbss-crypto/src/lib.rs"]})
    ids = [h["id"] for h in hz]
    assert "cbss-mpk-rpc-exposure" in ids
    h = next(h for h in hz if h["id"] == "cbss-mpk-rpc-exposure")
    # 关键: 这是否定性属性, 明确标注不可往返化 (否则又会被 roundtrip 漏掉)。
    assert h["invariant_able"] is False
    assert "mpk_g2" in h["prompt"]


def test_python_binding_diff_also_triggers_hazard():
    hz = PACK.security_hazards({"repo": "node", "diff_paths": ["cowboy-py/src/lib.rs"]})
    assert any(h["id"] == "cbss-mpk-rpc-exposure" for h in hz)


def test_cowboy_crypto_mirror_triggers_hazard():
    # esc-20260611-cbss-ibe-no-ephemeral: PR #672 把 IBE wrap kernel 镜像进新的
    # cowboy-crypto/ PyO3 crate, when_paths 没跟上 → classify 返回零 hazard,
    # confidentiality lens 没注入 review。钉死: kernel 的任何新家都必须触发。
    hz = PACK.security_hazards(
        {"repo": "node", "diff_paths": ["cowboy-crypto/src/lib.rs"]})
    ids = [h["id"] for h in hz]
    assert "cbss-mpk-rpc-exposure" in ids
    h = next(h for h in hz if h["id"] == "cbss-mpk-rpc-exposure")
    assert h["invariant_able"] is False


def test_non_crypto_diff_has_no_hazard():
    hz = PACK.security_hazards({"repo": "node", "diff_paths": ["execution/src/gas.rs"]})
    assert hz == []


def test_classify_surfaces_hazard_in_reasons_and_field():
    out = PACK.classify_detailed({"repo": "node", "diff_paths": ["cbss-crypto/src/lib.rs"]})
    assert out["tier"] == "high"
    assert any("security-hazard:cbss-mpk-rpc-exposure" in r for r in out["reasons"])
    assert any(h["id"] == "cbss-mpk-rpc-exposure" for h in out["security_hazards"])


def test_ratchet_guidance_blocks_roundtrip_for_negative_property():
    # confidentiality / auth 类根因 → 不可固化为 roundtrip 不变量, 走 review-hazard。
    for rc in ("confidentiality-break", "ind-cpa failure", "secret disclosure",
               "auth bypass", "privilege escalation"):
        g = PACK.ratchet_guidance(rc)
        assert g["invariant_able"] is False
        assert g["preferred_shape"] == "review-hazard"


def test_ratchet_guidance_allows_proptest_for_functional_property():
    for rc in ("econ-conservation", "state-consensus", "determinism-gap"):
        g = PACK.ratchet_guidance(rc)
        assert g["invariant_able"] is True
        assert g["preferred_shape"] == "proptest"


def test_wallet_signing_diff_triggers_blind_signing_hazard():
    # wallet 是 JS,无机械锚 —— 阶段二 review-hazard 是唯一覆盖形式。
    hz = PACK.security_hazards({"repo": "wallet", "diff_paths": ["src/lib/sign-binding.js"]})
    h = next((h for h in hz if h["id"] == "wallet-blind-signing-intent-binding"), None)
    assert h is not None
    assert h["invariant_able"] is False        # 否定性属性,不可往返化
    assert PACK.classify_detailed(
        {"repo": "wallet", "diff_paths": ["src/lib/sign-binding.js"]})["tier"] == "high"


def test_store_admin_privileged_diff_triggers_authz_hazard():
    hz = PACK.security_hazards(
        {"repo": "store-admin", "diff_paths": ["src/services/approval-orchestrator.ts"]})
    assert any(h["id"] == "store-admin-privileged-op-authz-and-db-boundary" for h in hz)


def test_review_invariants_are_catalog_only_never_mechanically_dispatched():
    # review-hazard 不变量必须在全量 catalog (供 reconcile seed),但绝不进 list_invariants
    # —— 否则机械门禁会去跑一条无 run_command 的检查,报假 degraded。
    catalog = {d.id: d for d in PACK.all_invariant_defs()}
    for iid, repo, path in [
            ("wallet.no_blind_signing_intent_binding", "wallet", "src/lib/sign-binding.js"),
            ("store-admin.privileged_op_server_authz", "store-admin",
             "src/services/approval-orchestrator.ts")]:
        d = catalog.get(iid)
        assert d is not None and d.executor_kind == "review-hazard" and d.run_command == []
        dispatched = {i.id for i in PACK.list_invariants({"repo": repo, "diff_paths": [path]})}
        assert iid not in dispatched
