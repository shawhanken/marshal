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
    assert len(store.list_invariants("cowboy", "node")) == 4


def test_handle_result_records_gate_run(db_session):
    store = Store(db_session)
    orch = Orchestrator(pack=CowboyPack(), store=store)
    ev = NormalizedEvent(kind="pr", repo="node", change_ref="abc123",
                         diff_paths=["docs/x.md"])
    job = orch.handle_event(ev)
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="ok",
        payload={"results": [{"invariant_id": "econ.fee_conservation",
                              "passed": True, "detail": ""}]})
    decision = orch.handle_result(ev, res)
    assert decision.verdict == "pass"
    from marshal_core.knowledge.models import GateRun
    assert db_session.query(GateRun).count() == 1
