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


def test_degraded_result_high_tier_escalate():
    gate = InvariantGate(pack=CowboyPack())
    ev = _event()
    job = gate.build_dispatch(ev)
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="degraded",
                           payload={"results": []})
    decision = gate.evaluate(ev, job, res)
    assert decision.verdict == "escalate"


def _low_tier_event():
    return NormalizedEvent(kind="pr", repo="node", change_ref="low456",
                           diff_paths=["README.md"])


def _all_pass_results(invariant_ids):
    return [{"invariant_id": i, "passed": True, "detail": ""} for i in invariant_ids]


def test_degraded_result_low_tier_escalates_not_pass():
    gate = InvariantGate(pack=CowboyPack())
    ev = _low_tier_event()
    job = gate.build_dispatch(ev)
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="error",
                           payload={"results": []})
    decision = gate.evaluate(ev, job, res)
    assert decision.verdict == "escalate"
    assert decision.gates[0]["outcome"] == "degraded"


def test_empty_results_with_expected_invariants_escalates():
    gate = InvariantGate(pack=CowboyPack())
    ev = _event()
    job = gate.build_dispatch(ev)
    assert job.params["invariant_ids"]      # precondition: plan expects invariants
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="ok",
                           payload={"results": []})
    decision = gate.evaluate(ev, job, res)
    assert decision.verdict == "escalate"
    assert decision.gates[0]["outcome"] == "degraded"


def test_missing_invariant_result_escalates():
    gate = InvariantGate(pack=CowboyPack())
    ev = _event()
    job = gate.build_dispatch(ev)
    partial = _all_pass_results(job.params["invariant_ids"][:-1])
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="ok",
                           payload={"results": partial})
    decision = gate.evaluate(ev, job, res)
    assert decision.verdict == "escalate"
    assert decision.gates[0]["outcome"] == "degraded"


def test_unknown_invariant_id_escalates():
    gate = InvariantGate(pack=CowboyPack())
    ev = _event()
    job = gate.build_dispatch(ev)
    padded = _all_pass_results(job.params["invariant_ids"] + ["not.a.real.invariant"])
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="ok",
                           payload={"results": padded})
    decision = gate.evaluate(ev, job, res)
    assert decision.verdict == "escalate"
    assert decision.gates[0]["outcome"] == "degraded"


def test_duplicate_invariant_results_escalate():
    gate = InvariantGate(pack=CowboyPack())
    ev = _event()
    job = gate.build_dispatch(ev)
    doubled = _all_pass_results(job.params["invariant_ids"]) \
        + _all_pass_results(job.params["invariant_ids"][:1])
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="ok",
                           payload={"results": doubled})
    decision = gate.evaluate(ev, job, res)
    assert decision.verdict == "escalate"
    assert decision.gates[0]["outcome"] == "degraded"


def test_job_id_mismatch_escalates():
    gate = InvariantGate(pack=CowboyPack())
    ev = _event()
    job = gate.build_dispatch(ev)
    res = StructuredResult(job_id="inv-someone-else", kind="invariant", status="ok",
        payload={"results": _all_pass_results(job.params["invariant_ids"])})
    decision = gate.evaluate(ev, job, res)
    assert decision.verdict == "escalate"
    assert decision.gates[0]["outcome"] == "degraded"


def test_kind_mismatch_escalates():
    gate = InvariantGate(pack=CowboyPack())
    ev = _event()
    job = gate.build_dispatch(ev)
    res = StructuredResult(job_id=job.job_id, kind="review", status="ok",
        payload={"results": _all_pass_results(job.params["invariant_ids"])})
    decision = gate.evaluate(ev, job, res)
    assert decision.verdict == "escalate"
    assert decision.gates[0]["outcome"] == "degraded"


def test_confirmed_failure_still_blocks_even_if_incomplete():
    gate = InvariantGate(pack=CowboyPack())
    ev = _event()
    job = gate.build_dispatch(ev)
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="ok",
        payload={"results": [{"invariant_id": job.params["invariant_ids"][0],
                              "passed": False, "detail": "assert failed"}]})
    decision = gate.evaluate(ev, job, res)
    assert decision.verdict == "block"


def test_non_boolean_passed_is_rejected_as_degraded():
    gate = InvariantGate(pack=CowboyPack())
    ev = _event()
    job = gate.build_dispatch(ev)
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="ok",
        payload={"results": [{"invariant_id": i, "passed": "false"}
                              for i in job.params["invariant_ids"]]})
    decision = gate.evaluate(ev, job, res)
    assert decision.verdict == "escalate"
    assert decision.gates[0]["outcome"] == "degraded"


def test_explicit_failure_blocks_even_with_degraded_status():
    gate = InvariantGate(pack=CowboyPack())
    ev = _event()
    job = gate.build_dispatch(ev)
    res = StructuredResult(job_id=job.job_id, kind="invariant", status="degraded",
        payload={"results": [{"invariant_id": job.params["invariant_ids"][0],
                              "passed": False}]})
    decision = gate.evaluate(ev, job, res)
    assert decision.verdict == "block"
    assert decision.gates[0]["outcome"] == "fail"
