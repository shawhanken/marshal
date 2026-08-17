from marshal_core.knowledge.models import ReviewJob
from marshal_core.knowledge.store import Store


def test_review_job_defaults(db_session):
    job = ReviewJob(change_ref="node#7", repo="node")
    db_session.add(job)
    db_session.commit()
    assert job.id is not None
    assert job.status == "pending"
    assert job.kind == "mechanical"
    assert job.requested_by == "dashboard"
    assert job.created_at is not None
    assert job.started_at is None
    assert job.finished_at is None
    assert job.result is None
    assert job.error is None


def test_enqueue_and_get_job_roundtrip(db_session):
    s = Store(db_session)
    job = s.enqueue_job(change_ref="node#7", repo="node")
    assert job["id"] is not None
    assert job["status"] == "pending"
    assert job["kind"] == "mechanical"
    fetched = s.get_job(job["id"])
    assert fetched["change_ref"] == "node#7"
    assert fetched["repo"] == "node"
    assert fetched["result"] is None


def test_get_job_missing_returns_none(db_session):
    s = Store(db_session)
    assert s.get_job(99999) is None


def test_claim_next_job_returns_oldest_pending_and_marks_running(db_session):
    s = Store(db_session)
    a = s.enqueue_job(change_ref="node#1")
    b = s.enqueue_job(change_ref="node#2")
    claimed = s.claim_next_job()
    assert claimed["id"] == a["id"]          # oldest first
    assert claimed["status"] == "running"
    assert claimed["started_at"] is not None
    # second claim gets the next pending
    claimed2 = s.claim_next_job()
    assert claimed2["id"] == b["id"]
    # nothing left
    assert s.claim_next_job() is None


def test_claim_skips_rows_already_running(db_session):
    # CAS guard: a row flipped to running behind the store's back must be skipped,
    # proving two workers can't both claim the same job.
    s = Store(db_session)
    job = s.enqueue_job(change_ref="node#9")
    row = db_session.get(ReviewJob, job["id"])
    row.status = "running"
    db_session.commit()
    assert s.claim_next_job() is None


def test_claim_retries_past_lost_race(db_session, monkeypatch):
    # Deterministically drive the CAS retry branch: intercept the first UPDATE so it
    # matches 0 rows (as if a competing worker grabbed job A between our SELECT and
    # UPDATE), then confirm claim_next_job retries and claims the next pending job B.
    s = Store(db_session)
    a = s.enqueue_job(change_ref="A")
    b = s.enqueue_job(change_ref="B")
    real_execute = s.s.execute
    state = {"first": True}

    class _Zero:
        rowcount = 0

    def fake_execute(stmt, *args, **kw):
        if state["first"]:
            state["first"] = False
            # simulate the competitor: flip A to running (flush so the retry SELECT
            # skips it) and report our UPDATE as having matched nothing
            db_session.get(ReviewJob, a["id"]).status = "running"
            db_session.flush()
            return _Zero()
        return real_execute(stmt, *args, **kw)

    monkeypatch.setattr(s.s, "execute", fake_execute)
    claimed = s.claim_next_job()
    assert claimed is not None
    assert claimed["id"] == b["id"]      # lost A, retried, claimed B
    assert claimed["status"] == "running"


def test_finish_job_sets_done_with_result(db_session):
    s = Store(db_session)
    job = s.enqueue_job(change_ref="node#1")
    s.claim_next_job()
    done = s.finish_job(job["id"], result={"invariant_ids": ["econ.fee_conservation"],
                                           "count": 1})
    assert done["status"] == "done"
    assert done["finished_at"] is not None
    assert done["result"]["count"] == 1


def test_fail_job_sets_failed_with_error(db_session):
    s = Store(db_session)
    job = s.enqueue_job(change_ref="node#1")
    s.claim_next_job()
    failed = s.fail_job(job["id"], error="boom")
    assert failed["status"] == "failed"
    assert failed["finished_at"] is not None
    assert failed["error"] == "boom"


def test_reclaim_stale_running_jobs(db_session):
    from datetime import datetime, timezone, timedelta
    s = Store(db_session)
    j = s.enqueue_job(change_ref="x")
    s.claim_next_job()                       # -> running, started_at ~now
    row = db_session.get(ReviewJob, j["id"])       # backdate to 2h ago (orphaned)
    row.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db_session.commit()
    assert s.reclaim_stale_jobs(older_than_s=1800) == 1
    assert s.get_job(j["id"])["status"] == "failed"


def test_reclaim_leaves_fresh_running_jobs(db_session):
    s = Store(db_session)
    j = s.enqueue_job(change_ref="y")
    s.claim_next_job()                       # running, started_at ~now
    assert s.reclaim_stale_jobs(older_than_s=1800) == 0
    assert s.get_job(j["id"])["status"] == "running"
