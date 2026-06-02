"""cbss / cbfs repo 覆盖 —— 领域高危路径 + 跨 repo 契约 (真实路径/真实锚点测试)。

补此之前: cbss/cbfs 只靠通用 crypto 子串偶然命中, 核心路径(erasure/placement/
release/dkg)会被误判成 mid, 且 0 跨 repo 契约。
"""
from marshal_pack_cowboy.pack import CowboyPack

PACK = CowboyPack()


# ---- cbss 高危分级 ----
def test_cbss_crypto_path_is_high():
    out = PACK.classify_detailed({"repo": "cbss",
                                  "diff_paths": ["crates/cbss-crypto/src/ibe.rs"]})
    assert out["tier"] == "high"
    assert any("threshold-IBE" in r for r in out["reasons"])
    # 真实 cbss 仓路径也应触发机密性危险点 (否定性属性)
    assert any(h["id"] == "cbss-mpk-rpc-exposure" for h in out["security_hazards"])


def test_cbss_volume_seal_daemon_path_is_high():
    out = PACK.classify_detailed(
        {"repo": "cbss", "diff_paths": ["crates/cbssd/src/cip9_volume_seal.rs"]})
    assert out["tier"] == "high"


# ---- cbfs 高危分级 ----
def test_cbfs_erasure_is_high():
    out = PACK.classify_detailed({"repo": "cbfs", "diff_paths": ["erasure/src/lib.rs"]})
    assert out["tier"] == "high"
    assert any("cbfs integrity" in r for r in out["reasons"])


def test_cbfs_placement_and_auth_are_high():
    for p in ("placement/src/lib.rs", "auth/src/lib.rs", "manifest/src/lib.rs"):
        out = PACK.classify_detailed({"repo": "cbfs", "diff_paths": [p]})
        assert out["tier"] == "high", p


# ---- 跨 repo 契约 ----
def test_cip9_ras_contract_fires_on_both_sides():
    cbfs = PACK.contracts_hit({"repo": "cbfs", "diff_paths": ["cowboy-ras/src/types.rs"]})
    assert "cip9-ras" in cbfs
    node = PACK.contracts_hit({"repo": "node", "diff_paths": ["ras/src/types.rs"]})
    assert "cip9-ras" in node


def test_cip24_cbss_contract_fires_on_both_sides():
    cbss = PACK.contracts_hit(
        {"repo": "cbss", "diff_paths": ["crates/cbss-types/src/lib.rs"]})
    assert "cip24-cbss" in cbss
    node = PACK.contracts_hit({"repo": "node", "diff_paths": ["types/src/cbss.rs"]})
    assert "cip24-cbss" in node


def test_contract_invariants_point_at_node_anchor_tests():
    # 契约校验跑在 node 侧 (字节兼容守护住在 node)。
    invs = {i.id: i for i in PACK.list_invariants(
        {"repo": "cbfs", "diff_paths": ["cowboy-ras/src/types.rs"]})}
    assert "contract.ras_canonical_vectors" in invs
    ras = invs["contract.ras_canonical_vectors"]
    assert ras.location_repo == "node"
    assert "locked_canonical_hashes_match_expected" in ras.run_command

    invs2 = {i.id: i for i in PACK.list_invariants(
        {"repo": "cbss", "diff_paths": ["crates/cbss-types/src/lib.rs"]})}
    assert "contract.cbss_wire_round_trip" in invs2
    assert invs2["contract.cbss_wire_round_trip"].location_repo == "node"


def test_cip9_and_cip24_now_in_conformance_matrix():
    m = PACK.conformance_matrix()
    assert "contract.ras_canonical_vectors" in m.get("CIP-9", [])
    assert "contract.cbss_wire_round_trip" in m.get("CIP-24", [])


def test_cbss_cbfs_ordinary_paths_still_not_overclassified():
    # 普通守护/配置路径不应被新规则误升 (除非命中契约/危险点)。
    assert PACK.classify({"repo": "cbss", "diff_paths": ["crates/cbssd/src/config.rs"]}) == "mid"
    assert PACK.classify({"repo": "cbfs", "diff_paths": ["cli/src/main.rs"]}) == "mid"
