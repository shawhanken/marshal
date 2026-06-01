import json
from marshal_core.contracts import (
    NormalizedEvent, DispatchJob, StructuredResult, GateDecision,
)


def test_normalized_event_roundtrip():
    ev = NormalizedEvent(kind="pr", repo="node", change_ref="abc123",
                         diff_paths=["execution/src/x.rs"], labels=[], actor="alice")
    dumped = ev.model_dump_json()
    assert NormalizedEvent.model_validate_json(dumped) == ev


def test_structured_result_invariant_payload():
    res = StructuredResult(
        job_id="j1", schema_version="1", kind="invariant",
        payload={"results": [{"invariant_id": "econ.fee_conservation",
                              "passed": True, "detail": ""}]},
        cost=0.0, status="ok")
    assert res.payload["results"][0]["passed"] is True


def test_gate_decision_verdict_enum():
    d = GateDecision(change_ref="abc123", tier="mid",
                     gates=[{"name": "invariants", "outcome": "pass", "evidence_ref": "run:1"}],
                     verdict="pass")
    assert d.verdict == "pass"
    assert "properties" in json.loads(json.dumps(GateDecision.model_json_schema()))
