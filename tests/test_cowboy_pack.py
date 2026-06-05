from marshal_pack_cowboy.pack import CowboyPack


def test_pack_id():
    assert CowboyPack().id == "cowboy"


def test_lists_econ_invariants_for_node():
    pack = CowboyPack()
    invs = pack.list_invariants(scope={"repo": "node", "diff_paths": []})
    ids = {i.id for i in invs}
    assert "econ.fee_conservation" in ids
    assert "econ.settlement_sum_100" in ids
    assert all(i.location_repo == "node" for i in invs)


def test_classify_econ_path_is_high_or_mid():
    pack = CowboyPack()
    tier = pack.classify({"repo": "node",
                          "diff_paths": ["execution/src/execution/transaction.rs"]})
    assert tier in ("high", "mid")


def test_invariants_carry_run_command():
    from marshal_pack_cowboy.pack import CowboyPack
    invs = CowboyPack().list_invariants({"repo": "node", "diff_paths": []})
    by_id = {i.id: i for i in invs}
    cmd = by_id["econ.fee_conservation"].run_command
    assert cmd[:3] == ["cargo", "test", "-p"]
    # Real, verified test name on node devnet is econ_fee_conservation (NOT
    # prop_*); module-qualified so `cargo test ... --exact` actually matches it.
    # (An earlier phantom `prop_fee_conservation` silently matched 0 tests.)
    assert "econ_invariants::econ_fee_conservation" in cmd
