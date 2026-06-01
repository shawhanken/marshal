from marshal_core.knowledge.store import Store


def test_register_and_list_invariants(db_session):
    store = Store(db_session)
    store.register_invariant(id="econ.fee_conservation", domain_pack="cowboy",
                             domain="econ", spec_ref="CIP-3", executor_kind="proptest",
                             location_repo="node", location_path="execution/src/econ_invariants.rs",
                             location_test="prop_fee_conservation", severity="high")
    got = store.list_invariants(domain_pack="cowboy", repo="node")
    assert len(got) == 1
    assert got[0].id == "econ.fee_conservation"


def test_record_gate_run(db_session):
    store = Store(db_session)
    run = store.record_gate_run(change_ref="abc123", job_id="j1",
                                verdict="pass", evidence={"gates": []})
    assert run.id is not None
    assert store.get_gate_run(run.id).verdict == "pass"
