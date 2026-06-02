from marshal_core.knowledge.store import Store


def _seed(s):
    s.register_invariant(id="i.hand", domain_pack="cowboy", domain="econ",
                         executor_kind="test", location_repo="node",
                         location_path="p", location_test="t", origin="hand")
    s.register_invariant(id="i.ratchet", domain_pack="cowboy", domain="state",
                         executor_kind="test", location_repo="node",
                         location_path="p", location_test="t", origin="ratchet")
    s.open_escape(id="e1", description="d", root_cause_class="c")
    s.open_escape(id="e2", description="d", root_cause_class="c")
    s.close_escape("e2", spawned_check="i.ratchet")
    s.record_gate_run(change_ref="r1", job_id="r1", verdict="pass", evidence={})
    s.record_gate_run(change_ref="r2", job_id="r2", verdict="needs_human", evidence={})


def test_metrics_counts(db_session):
    s = Store(db_session)
    _seed(s)
    m = s.metrics()
    assert m["invariant_gate_count"] == 2
    assert m["ratchet_invariants"] == 1
    assert m["escapes_open"] == 1
    assert m["escapes_closed"] == 1
    assert m["ratchet_increment"] == 1
    assert m["gate_runs_total"] == 2
    assert m["gate_runs_by_verdict"] == {"pass": 1, "block": 0, "needs_human": 1}


def test_metrics_marks_unavailable_honestly(db_session):
    s = Store(db_session)
    m = s.metrics()
    for k in ("escape_rate", "mean_time_to_detection", "tiered_review_coverage"):
        assert k in m["unavailable"]
