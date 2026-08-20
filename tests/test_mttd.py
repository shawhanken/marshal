from datetime import datetime, timezone, timedelta
from marshal_core.knowledge.store import Store


def test_mttd_computes_over_timestamped_escapes(db_session):
    s = Store(db_session)
    t0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    s.open_escape(id="e1", description="d", root_cause_class="c",
                  introduced_at_ts=t0, discovered_at=t0 + timedelta(days=4))
    s.open_escape(id="e2", description="d", root_cause_class="c",
                  introduced_at_ts=t0, discovered_at=t0 + timedelta(days=6))
    s.open_escape(id="e3", description="d", root_cause_class="c")  # no ts -> excluded
    m = s.mttd()
    assert m["count"] == 2
    assert m["mean_days"] == 5.0
    assert m["excluded"] == 1


def test_mttd_no_timestamped_escapes_is_honest(db_session):
    s = Store(db_session)
    s.open_escape(id="e1", description="d", root_cause_class="c")
    m = s.mttd()
    assert m["mean_days"] is None
    assert m["count"] == 0
    assert m["excluded"] == 1
