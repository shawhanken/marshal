from marshal_pack_cowboy.pack import CowboyPack


def test_conformance_matrix_maps_spec_to_invariants():
    pack = CowboyPack()
    m = pack.conformance_matrix()
    # CIP-4 is covered by the whole state-consensus family (3).
    assert set(m["CIP-4"]) == {
        "state.root_consistent_propose_verify_report",
        "state.speculative_rollback_equivalent",
        "state.root_reflects_committed_set",
    }
    # CIP-3 covered by fee conservation; WP by the tx-encoding contract.
    assert "econ.fee_conservation" in m["CIP-3"]
    assert "contract.tx_encoding_roundtrip" in m["WP"]


def test_conformance_matrix_only_counts_resolvable_specs():
    pack = CowboyPack()
    m = pack.conformance_matrix()
    # No unresolvable placeholder keys (e.g. the old "CIP-?"/"C-1").
    for ref in m:
        assert pack.resolve_spec_ref(ref) is not None, ref
