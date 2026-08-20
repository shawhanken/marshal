import importlib
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "dash.db"
    monkeypatch.setenv("MARSHAL_DB", f"sqlite:///{db_file}")
    import marshal_core.adapters.api as api
    importlib.reload(api)
    # seed via the same engine the app now uses
    from marshal_core.knowledge.store import Store
    with api._Session() as s:
        st = Store(s)
        st.record_gate_run(change_ref="node#2", job_id="j2", verdict="needs_human",
                           evidence={"pr": 2, "repo": "node", "tier": "high"})
        st.record_gate_run(change_ref="node#9", job_id="j9", verdict="pass", evidence={})
    return TestClient(api.app)


def test_runs_endpoint_returns_evidence(client):
    import marshal_core.adapters.api as api
    from marshal_core.knowledge.store import Store
    with api._Session() as s:
        run_id = Store(s).record_gate_run(
            change_ref="node#2", job_id="j2x", verdict="needs_human",
            evidence={"pr": 2, "repo": "node", "tier": "high"}).id
    r = client.get(f"/api/runs/{run_id}")
    assert r.status_code == 200
    assert r.json()["change_ref"] == "node#2"


def test_runs_endpoint_404_on_missing(client):
    r = client.get("/api/runs/99999")
    assert r.status_code == 404


def test_health_composes_metrics_and_breakdowns(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    # verdict distribution comes straight from metrics()
    assert body["gate_runs_by_verdict"]["needs_human"] == 1
    assert body["gate_runs_by_verdict"]["pass"] == 1
    # new aggregate blocks are present
    assert "escape_breakdown" in body
    assert "invariant_breakdown" in body
    assert "verdict_timeseries" in body
    # honest gaps carried through (but MTTD now computed in Phase 4)
    assert "mean_time_to_detection" not in body["unavailable"]
    assert "mean_time_to_detection" in body                      # now a real top-level metric
    assert "count" in body["mean_time_to_detection"]


def test_escapes_endpoint_returns_breakdown(client):
    with_escape = client.get("/api/escapes")
    assert with_escape.status_code == 200
    assert isinstance(with_escape.json(), list)


def test_root_serves_spa(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Marshal" in r.text
    assert 'id="app"' in r.text


def test_spa_has_rereview_button_wiring(client):
    html = client.get("/").text
    assert "re-review" in html
    assert "startJob" in html
    assert "/api/jobs" in html


def test_rereview_button_has_no_inline_onclick_handler(client):
    # Security regression guard: the button must NOT be wired via an inline
    # onclick that interpolates change_ref/repo (esc() is the wrong encoding for
    # the inline-handler JS sink and is bypassable). It must use addEventListener.
    html = client.get("/").text
    assert 'onclick="startJob' not in html
    assert "addEventListener" in html


def test_spa_has_deep_review_button(client):
    html = client.get("/").text
    assert "deep review" in html          # the new deep button label
    assert "'deep'" in html                # startJob(..., 'deep') wiring
    assert "re-review" in html             # mechanical button still present
    assert 'onclick="startJob' not in html # still no inline handler


def test_spa_locks_both_buttons_during_job(client):
    # UX race guard: a running job disables BOTH buttons in the row (collective
    # querySelectorAll('.btn')), so a user can't start a second concurrent job on
    # the same card and clobber the shared status span.
    html = client.get("/").text
    assert "querySelectorAll('.btn')" in html


def test_spa_renders_real_health_metrics(client):
    html = client.get("/").text
    # the Health page renders real computed metrics from /api/health, not placeholders
    assert "gate_runs_by_verdict" in html
    assert "verdict_timeseries" in html
    assert "pending Phase 4" not in html          # placeholder is gone


def test_inbox_returns_pr_queue(client, monkeypatch):
    import marshal_core.pr_inbox as pri
    monkeypatch.setenv("MARSHAL_INBOX_TTL_S", "0")
    monkeypatch.setattr(pri, "build_inbox",
                        lambda s, repos=None: [{"repo": "node", "number": 7, "eligible": True}])
    r = client.get("/api/inbox")
    assert r.status_code == 200
    body = r.json()
    assert body["prs"] == [{"repo": "node", "number": 7, "eligible": True}]
    assert "github_token" in body and "repos" in body


def test_spa_renders_pr_queue(client):
    html = client.get("/").text
    assert "renderInbox" in html
    assert "on hold" in html           # blocked badge label
    assert "github_token" in html      # SPA reads the token flag for the empty-state hint


def test_inbox_survives_build_failure(client, monkeypatch):
    import marshal_core.pr_inbox as pri
    monkeypatch.setenv("MARSHAL_INBOX_TTL_S", "0")
    def boom(s, repos=None):
        raise RuntimeError("github down")
    monkeypatch.setattr(pri, "build_inbox", boom)
    r = client.get("/api/inbox")
    assert r.status_code == 200          # no 500
    assert r.json()["prs"] == []


def test_spa_has_stale_badge(client):
    html = client.get("/").text
    assert "stale-badge" in html and "🔄" in html


def test_worker_endpoint_down_without_heartbeat(client):
    # no worker has written a heartbeat -> the dashboard must show it as down
    j = client.get("/api/worker").json()
    assert j["state"] == "down"
    assert j["alive"] is False
    assert j["current"] is None
    assert j["queue"] == {"pending": 0, "running": 0, "done": 0, "failed": 0}


def test_worker_endpoint_idle_with_fresh_heartbeat(client):
    from datetime import datetime, timezone
    import marshal_core.adapters.api as api
    from marshal_core.knowledge.store import Store
    with api._Session() as s:
        Store(s).set_meta("worker:heartbeat", datetime.now(timezone.utc).isoformat())
    j = client.get("/api/worker").json()
    assert j["state"] == "idle"
    assert j["alive"] is True
    assert j["seconds_ago"] is not None and j["seconds_ago"] < 15


def test_worker_endpoint_busy_when_job_running(client):
    from datetime import datetime, timezone
    import marshal_core.adapters.api as api
    from marshal_core.knowledge.store import Store
    with api._Session() as s:
        st = Store(s)
        st.set_meta("worker:heartbeat", datetime.now(timezone.utc).isoformat())
        st.enqueue_job(change_ref="deadbeef", repo="node", kind="deep")
        claimed = st.claim_next_job()          # -> running
        assert claimed["status"] == "running"
    j = client.get("/api/worker").json()
    assert j["state"] == "busy"
    assert j["current"]["kind"] == "deep"
    assert j["current"]["repo"] == "node"
    assert j["queue"]["running"] == 1
    # elapsed is computed server-side (tz-proof), non-negative, and freshly small
    assert j["current"]["elapsed_s"] >= 0
    assert j["current"]["elapsed_s"] < 60


def test_worker_busy_even_if_heartbeat_stale(client):
    # a long deep job blocks the loop so the heartbeat goes stale, but a running
    # job means the worker is alive and busy — must NOT read as down
    import marshal_core.adapters.api as api
    from marshal_core.knowledge.store import Store
    with api._Session() as s:
        st = Store(s)
        st.set_meta("worker:heartbeat", "2000-01-01T00:00:00+00:00")   # ancient
        st.enqueue_job(change_ref="deadbeef", repo="node", kind="deep")
        st.claim_next_job()
    j = client.get("/api/worker").json()
    assert j["state"] == "busy"
    assert j["alive"] is False


def test_gate_runs_endpoint_lists_recent(client):
    r = client.get("/api/gate-runs?limit=10")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert isinstance(runs, list) and len(runs) >= 2          # fixture seeded 2 gate runs
    assert {"id", "change_ref", "verdict", "created_at", "repo", "pr"} <= set(runs[0])
    assert runs[0]["id"] >= runs[-1]["id"]                    # newest first


def test_invariants_endpoint_lists(client):
    r = client.get("/api/invariants")
    assert r.status_code == 200
    assert isinstance(r.json()["invariants"], list)           # empty in the bare test DB


def test_reconcile_dry_run_reports_missing_without_writing(client):
    before = len(client.get("/api/invariants").json()["invariants"])
    d = client.post("/api/reconcile", json={"apply": False}).json()
    assert d["applied"] is False
    assert d["counts"]["added"] > 0                            # empty DB -> catalog all missing
    assert "coverage_gaps" in d
    after = len(client.get("/api/invariants").json()["invariants"])
    assert after == before                                    # dry-run wrote nothing


def test_reconcile_apply_seeds_and_is_idempotent(client):
    d = client.post("/api/reconcile", json={"apply": True}).json()
    assert d["applied"] is True and d["counts"]["added"] > 0
    ids = {x["id"] for x in client.get("/api/invariants").json()["invariants"]}
    assert "cbqs.at_least_once_safe_prefix_holds" in ids
    again = client.post("/api/reconcile", json={"apply": False}).json()
    assert again["counts"]["added"] == 0                       # nothing left to add


def test_escape_timeline_endpoint_cumulative(client):
    from datetime import datetime, timezone
    import marshal_core.adapters.api as api
    from marshal_core.knowledge.models import EscapeRegistry
    with api._Session() as s:
        s.add(EscapeRegistry(id="e1", discovered_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                             root_cause_class="x", status="closed"))
        s.add(EscapeRegistry(id="e2", discovered_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                             root_cause_class="y", status="open"))
        s.add(EscapeRegistry(id="e3", discovered_at=datetime(2026, 6, 3, tzinfo=timezone.utc),
                             root_cause_class="z", status="closed"))
        s.commit()
    tl = client.get("/api/escape-timeline").json()["timeline"]
    assert [d["cumulative"] for d in tl] == [2, 3]         # 2 on 06-01, then +1 on 06-03
    assert tl[0]["date"] == "2026-06-01" and tl[1]["date"] == "2026-06-03"


def test_worker_endpoint_includes_active_jobs(client):
    from datetime import datetime, timezone
    import marshal_core.adapters.api as api
    from marshal_core.knowledge.store import Store
    with api._Session() as s:
        st = Store(s)
        st.set_meta("worker:heartbeat", datetime.now(timezone.utc).isoformat())
        st.enqueue_job(change_ref="aaa", repo="node", kind="deep")     # oldest
        st.enqueue_job(change_ref="bbb", repo="cbss", kind="mechanical")
        st.claim_next_job()                                            # oldest -> running
    jobs = client.get("/api/worker").json()["jobs"]
    assert len(jobs) == 2
    assert jobs[0]["status"] == "running" and jobs[0]["repo"] == "node"   # running first
    assert jobs[1]["status"] == "pending"
    assert "elapsed_s" in jobs[0]
