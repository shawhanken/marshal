"""`escalate` is the renamed-forward verdict for `needs_human`; the dashboard must
treat them as one bucket across inbox / metrics / timeseries."""
from datetime import datetime, timezone
from marshal_core.knowledge.models import GateRun
from marshal_core.knowledge.store import Store


def _seed_mixed(s):
    s.record_gate_run(change_ref="old", job_id="o", verdict="needs_human", evidence={})
    s.record_gate_run(change_ref="new", job_id="n", verdict="escalate", evidence={})
    s.record_gate_run(change_ref="p", job_id="p", verdict="pass", evidence={})
    s.record_gate_run(change_ref="b", job_id="b", verdict="block", evidence={})


def test_inbox_includes_both_needs_human_and_escalate(db_session):
    s = Store(db_session)
    _seed_mixed(s)
    refs = {r["change_ref"] for r in s.list_needs_human()}
    assert refs == {"old", "new"}          # both the old and renamed verdict, not pass/block


def test_metrics_folds_escalate_into_needs_human(db_session):
    s = Store(db_session)
    _seed_mixed(s)
    v = s.metrics()["gate_runs_by_verdict"]
    assert v["needs_human"] == 2            # needs_human + escalate
    assert v["pass"] == 1
    assert v["block"] == 1


def test_verdict_timeseries_folds_escalate(db_session):
    s = Store(db_session)
    day = datetime(2026, 8, 12, 10, tzinfo=timezone.utc)
    db_session.add(GateRun(change_ref="a", job_id="a", verdict="escalate", evidence={},
                           created_at=day))
    db_session.add(GateRun(change_ref="b", job_id="b", verdict="needs_human", evidence={},
                           created_at=day))
    db_session.commit()
    ts = s.verdict_timeseries()
    assert ts == [{"date": "2026-08-12", "pass": 0, "needs_human": 2, "block": 0}]
