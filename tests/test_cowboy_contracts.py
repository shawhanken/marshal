from marshal_pack_cowboy.pack import CowboyPack


def test_wallet_tx_encoding_change_hits_contract():
    pack = CowboyPack()
    hit = pack.contracts_hit({"repo": "wallet",
                              "diff_paths": ["src/lib/cbor.js"]})
    assert "tx-encoding" in hit


def test_node_transaction_change_hits_tx_contract():
    pack = CowboyPack()
    hit = pack.contracts_hit({"repo": "node",
                              "diff_paths": ["types/src/execution.rs"]})
    assert "tx-encoding" in hit


def test_runner_types_change_hits_runner_contract():
    pack = CowboyPack()
    hit = pack.contracts_hit({"repo": "runner",
                              "diff_paths": ["crates/runner-common/src/types.rs"]})
    assert "runner-types" in hit


def test_unrelated_change_hits_nothing():
    pack = CowboyPack()
    hit = pack.contracts_hit({"repo": "node",
                              "diff_paths": ["rpc/src/handlers.rs"]})
    assert hit == []


def test_wallet_change_surfaces_tx_contract_invariant():
    pack = CowboyPack()
    invs = pack.list_invariants({"repo": "wallet",
                                 "diff_paths": ["src/lib/cbor.js"]})
    ids = [i.id for i in invs]
    assert "contract.tx_encoding_roundtrip" in ids
    # 该契约不变量本体住在 node
    inv = next(i for i in invs if i.id == "contract.tx_encoding_roundtrip")
    assert inv.location_repo == "node"


def test_node_econ_change_still_lists_econ_invariants():
    pack = CowboyPack()
    invs = pack.list_invariants({"repo": "node",
                                 "diff_paths": ["execution/src/execution/transaction.rs"]})
    assert "econ.fee_conservation" in [i.id for i in invs]


def test_node_storage_change_surfaces_state_invariants():
    pack = CowboyPack()
    invs = pack.list_invariants({"repo": "node",
                                 "diff_paths": ["storage/src/blockchain_storage.rs"]})
    ids = [i.id for i in invs]
    assert "state.root_consistent_propose_verify_report" in ids
    assert "state.speculative_rollback_equivalent" in ids
    assert "state.root_reflects_committed_set" in ids


def test_node_chain_change_surfaces_state_invariants():
    pack = CowboyPack()
    invs = pack.list_invariants({"repo": "node", "diff_paths": ["chain/src/fast_sync.rs"]})
    assert "state.root_consistent_propose_verify_report" in [i.id for i in invs]


def test_node_nonstate_change_omits_state_invariants():
    pack = CowboyPack()
    invs = pack.list_invariants({"repo": "node", "diff_paths": ["rpc/src/handlers/chain.rs"]})
    ids = [i.id for i in invs]
    assert "state.root_consistent_propose_verify_report" not in ids
