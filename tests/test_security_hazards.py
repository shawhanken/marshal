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
