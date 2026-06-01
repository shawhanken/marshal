from marshal_core.contracts import NormalizedEvent, StructuredResult
from marshal_core.modules.invariant_gate import InvariantGate
from marshal_pack_cowboy.pack import CowboyPack


def _event():
    return NormalizedEvent(kind="pr", repo="node", change_ref="abc123",
                           diff_paths=["execution/src/execution/transaction.rs"])


def test_build_dispatch_lists_applicable_invariants():
    gate = InvariantGate(pack=CowboyPack())
    job = gate.build_dispatch(_event())
    assert job.kind == "invariant"
    assert job.target_repo == "node"
    assert set(job.params["invariant_ids"]) >= {
        "econ.fee_conservation", "econ.settlement_sum_100", "econ.escrow_non_negative"}


def test_ingest_all_pass_is_pass():
    gate = InvariantGate(pack=CowboyPack())
    job = gate.build_dispatch(_event())
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="ok",
        payload={"results": [{"invariant_id": i, "passed": True, "detail": ""}
                            for i in job.params["invariant_ids"]]})
    decision = gate.evaluate(_event(), job, res)
    assert decision.verdict == "pass"


def test_ingest_any_fail_is_block():
    gate = InvariantGate(pack=CowboyPack())
    job = gate.build_dispatch(_event())
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="ok",
        payload={"results": [{"invariant_id": "econ.fee_conservation",
                              "passed": False, "detail": "burn+tip != fee"}]})
    decision = gate.evaluate(_event(), job, res)
    assert decision.verdict == "block"


def test_degraded_result_high_tier_needs_human():
    gate = InvariantGate(pack=CowboyPack())
    ev = _event()
    job = gate.build_dispatch(ev)
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="degraded",
                           payload={"results": []})
    decision = gate.evaluate(ev, job, res)
    assert decision.verdict == "needs_human"
