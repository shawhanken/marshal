from datetime import datetime, timezone
from marshal_core.knowledge.models import GateRun
from marshal_core.knowledge.store import Store


def _seed_runs(s):
    s.record_gate_run(change_ref="node#1", job_id="j1", verdict="pass", evidence={})
    s.record_gate_run(change_ref="node#2", job_id="j2", verdict="needs_human",
                      evidence={"pr": 2, "repo": "node", "tier": "high", "cip": "CIP-3",
                                "dimensions": ["econ"], "invariants_run": 5,
                                "invariants_pass": 5, "high_sev_findings": 0,
                                "advisory_findings": ["a1"]})
    s.record_gate_run(change_ref="node#3", job_id="j3", verdict="needs_human",
                      evidence={"pr": 3, "repo": "runner", "tier": "mid"})
    s.record_gate_run(change_ref="node#4", job_id="j4", verdict="block", evidence={})


def test_list_needs_human_returns_only_needs_human_newest_first(db_session):
    s = Store(db_session)
    _seed_runs(s)
    rows = s.list_needs_human()
    assert [r["change_ref"] for r in rows] == ["node#3", "node#2"]
    assert rows[0]["id"] is not None
    assert rows[0]["verdict"] == "needs_human"
    assert rows[1]["evidence"]["tier"] == "high"


def test_list_needs_human_respects_limit(db_session):
    s = Store(db_session)
    _seed_runs(s)
    assert len(s.list_needs_human(limit=1)) == 1


def test_escape_breakdown_groups_by_root_cause_all_statuses(db_session):
    s = Store(db_session)
    s.open_escape(id="e1", description="d", root_cause_class="econ-conservation")
    s.open_escape(id="e2", description="d", root_cause_class="econ-conservation")
    s.open_escape(id="e3", description="d", root_cause_class="state-consensus")
    s.close_escape("e2", spawned_check="i.x")
    rows = s.escape_breakdown()
    by_class = {r["root_cause_class"]: r for r in rows}
    assert by_class["econ-conservation"]["count"] == 2
    assert by_class["econ-conservation"]["open"] == 1
    assert by_class["econ-conservation"]["closed"] == 1
    assert by_class["state-consensus"]["count"] == 1
    # sorted by count desc so the worst-offending class is first
    assert rows[0]["count"] >= rows[-1]["count"]


def test_invariant_breakdown_counts_status_severity_and_lists_candidate_red(db_session):
    s = Store(db_session)
    common = dict(domain_pack="cowboy", domain="econ", executor_kind="test",
                  location_repo="node", location_path="p", location_test="t")
    s.register_invariant(id="i.a", severity="high", status="active", **common)
    s.register_invariant(id="i.b", severity="high", status="active", **common)
    s.register_invariant(id="i.c", severity="medium", status="active", **common)
    s.register_invariant(id="i.red", severity="high", status="candidate-red", **common)
    b = s.invariant_breakdown()
    assert b["by_status"]["active"] == 3
    assert b["by_status"]["candidate-red"] == 1
    assert b["by_severity"]["high"] == 3
    assert b["by_severity"]["medium"] == 1
    assert b["candidate_red_ids"] == ["i.red"]


def test_verdict_timeseries_buckets_by_day(db_session):
    s = Store(db_session)
    # insert runs on two distinct days with explicit created_at
    db_session.add(GateRun(change_ref="a", job_id="a", verdict="pass", evidence={},
                           created_at=datetime(2026, 6, 1, 10, tzinfo=timezone.utc)))
    db_session.add(GateRun(change_ref="b", job_id="b", verdict="needs_human", evidence={},
                           created_at=datetime(2026, 6, 1, 12, tzinfo=timezone.utc)))
    db_session.add(GateRun(change_ref="c", job_id="c", verdict="pass", evidence={},
                           created_at=datetime(2026, 6, 2, 9, tzinfo=timezone.utc)))
    db_session.commit()
    ts = s.verdict_timeseries()
    assert ts == [
        {"date": "2026-06-01", "pass": 1, "needs_human": 1, "block": 0},
        {"date": "2026-06-02", "pass": 1, "needs_human": 0, "block": 0},
    ]
