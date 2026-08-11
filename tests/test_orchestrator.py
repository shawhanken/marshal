from marshal_core.contracts import NormalizedEvent, StructuredResult
from marshal_core.knowledge.store import Store
from marshal_core.modules.orchestrator import Orchestrator
from marshal_pack_cowboy.pack import CowboyPack


def test_handle_event_returns_invariant_job_and_seeds_registry(db_session):
    store = Store(db_session)
    orch = Orchestrator(pack=CowboyPack(), store=store)
    ev = NormalizedEvent(kind="pr", repo="node", change_ref="abc123",
                         diff_paths=["execution/src/execution/transaction.rs"])
    job = orch.handle_event(ev)
    assert job.kind == "invariant"
    # The always-on node econ family: 4 fee/settlement/escrow proptests plus the
    # ratchet-grown econ.timer_burn_conservation (esc-20260605-timer-burn) and
    # econ.fee_waterfall_alias_conservation (esc-20260727).
    seeded = {i.id for i in store.list_invariants("cowboy", "node")}
    assert seeded == {
        "econ.fee_conservation",
        "econ.settlement_sum_100",
        "econ.escrow_non_negative",
        "econ.tx_fee_conservation",
        "econ.timer_burn_conservation",
        "econ.fee_waterfall_alias_conservation",
    }


def test_handle_result_records_gate_run(db_session):
    store = Store(db_session)
    orch = Orchestrator(pack=CowboyPack(), store=store)
    ev = NormalizedEvent(kind="pr", repo="node", change_ref="abc123",
                         diff_paths=["docs/x.md"])
    job = orch.handle_event(ev)
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="ok",
        payload={"results": [{"invariant_id": i, "passed": True, "detail": ""}
                             for i in job.params["invariant_ids"]]})
    decision = orch.handle_result(ev, res)
    assert decision.verdict == "pass"
    from marshal_core.knowledge.models import GateRun
    assert db_session.query(GateRun).count() == 1
