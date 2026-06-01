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
