from marshal_pack_cowboy.pack import CowboyPack


def test_execution_path_is_high_with_reason():
    pack = CowboyPack()
    d = pack.classify_detailed({"repo": "node",
                                "diff_paths": ["execution/src/execution/engine.rs"]})
    assert d["tier"] == "high"
    assert any("execution" in r for r in d["reasons"])


def test_system_address_change_is_high():
    pack = CowboyPack()
    d = pack.classify_detailed({"repo": "node",
                                "diff_paths": ["execution/src/runner/registry.rs"],
                                "diff_text": "Address::from_low_u64(0x91)"})
    assert d["tier"] == "high"


def test_contract_hit_forces_high():
    pack = CowboyPack()
    d = pack.classify_detailed({"repo": "wallet",
                                "diff_paths": ["src/lib/cbor.js"]})
    assert d["tier"] == "high"
    assert "tx-encoding" in d["contracts_hit"]
    assert any("cross_repo_contract" in r for r in d["reasons"])


def test_docs_only_is_low():
    pack = CowboyPack()
    d = pack.classify_detailed({"repo": "node", "diff_paths": ["README.md"]})
    assert d["tier"] == "low"


def test_rpc_handler_is_mid():
    pack = CowboyPack()
    d = pack.classify_detailed({"repo": "node", "diff_paths": ["rpc/src/handlers.rs"]})
    assert d["tier"] == "mid"


def test_classify_str_still_returns_tier():
    pack = CowboyPack()
    assert pack.classify({"repo": "node",
                          "diff_paths": ["execution/src/execution/engine.rs"]}) == "high"


def test_review_plan_scales_with_tier():
    pack = CowboyPack()
    assert len(pack.review_plan("high")) == 6
    assert len(pack.review_plan("mid")) == 3
    assert len(pack.review_plan("low")) == 1
