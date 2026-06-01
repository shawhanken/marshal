from marshal_pack_cowboy.pack import CowboyPack


def test_wallet_tx_encoding_change_hits_contract():
    pack = CowboyPack()
    hit = pack.contracts_hit({"repo": "wallet",
                              "diff_paths": ["src/tx/encode.js"]})
    assert "tx-encoding" in hit


def test_node_transaction_change_hits_tx_contract():
    pack = CowboyPack()
    hit = pack.contracts_hit({"repo": "node",
                              "diff_paths": ["types/src/transaction.rs"]})
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
