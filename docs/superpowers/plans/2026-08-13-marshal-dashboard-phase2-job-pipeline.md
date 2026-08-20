# Marshal Dashboard — Phase 2 (Job Pipeline + Mechanical Re-plan) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the job pipeline (a `review_job` state machine, `POST/GET /api/jobs`, and a background worker) plus a dashboard **re-review button** that runs a *mechanical re-plan* — proving the whole pipe end-to-end so Phase 3's deep worker drops into the same abstraction.

**Architecture:** A new `review_job` table decouples the web layer (enqueues jobs) from a separate worker process (claims jobs via compare-and-swap, runs them, writes results back). The worker's only Phase-2 job kind is `mechanical`: it rebuilds a `NormalizedEvent` and calls the existing `Orchestrator.plan()`, which re-selects/registers the applicable invariants. **A mechanical re-plan does NOT produce a new gate_run verdict** — computing a fresh verdict requires actually running the invariant tests, which is the Phase 3 deep worker's job. The SPA gets a re-review button + a live job-status row.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, pydantic, pytest + FastAPI `TestClient`. Worker is a plain Python module. Frontend is dependency-free vanilla JS.

**Scope note:** This is Phase 2 of the design spec (`docs/superpowers/specs/2026-08-13-marshal-dashboard-design.md`, §8.2). Phase 3 (headless-Claude *deep* worker producing real verdicts, worktree isolation, rate-limit, timeout) and Phase 4 (`introduced_at_ts`/MTTD) are **out of scope**. A `deep`-kind job enqueued in Phase 2 is accepted by the API but **failed gracefully** by the worker with a "not available until Phase 3" message — no crash, honest status.

**Faithful refinement of the spec:** Spec §4.1 listed `result_run_id` (FK to gate_run) on `review_job`. Because a mechanical re-plan produces a *plan*, not a gate_run, this plan generalizes that to a nullable `result` JSON column (mechanical stores `{invariant_ids, count}`; Phase 3 deep will store `{verdict, gate_run_id}` in the same column). Documented here so the divergence from the spec text is intentional and traceable.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/marshal_core/knowledge/models.py` | Add the `ReviewJob` ORM model | Modify |
| `src/marshal_core/knowledge/store.py` | Job lifecycle methods: enqueue / get / atomic claim / finish / fail | Modify |
| `src/marshal_core/adapters/api.py` | `POST /api/jobs` (localhost+token) and `GET /api/jobs/{id}` | Modify |
| `src/marshal_core/worker.py` | Worker: `run_once()` (mechanical handler) + `main()` loop | Create |
| `src/marshal_core/adapters/static/index.html` | Re-review button + live job-status row on inbox cards | Modify |
| `tests/test_review_job_store.py` | Store job-lifecycle unit tests | Create |
| `tests/test_jobs_api.py` | `/api/jobs` contract + token-guard tests | Create |
| `tests/test_worker.py` | `run_once` mechanical/deep/failure tests | Create |

---

## Task 1: `ReviewJob` model

**Files:**
- Modify: `src/marshal_core/knowledge/models.py`
- Test: `tests/test_review_job_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_review_job_store.py`:

```python
from marshal_core.knowledge.models import ReviewJob


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_review_job_store.py -v`
Expected: FAIL with `ImportError: cannot import name 'ReviewJob'`

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/knowledge/models.py`, add this class at the end of the file (the imports `String, Integer, JSON, DateTime`, `Mapped/mapped_column`, and `_now` already exist at the top):

```python
class ReviewJob(Base):
    __tablename__ = "review_job"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    change_ref: Mapped[str] = mapped_column(String, index=True)
    repo: Mapped[str] = mapped_column(String, default="node")
    kind: Mapped[str] = mapped_column(String, default="mechanical")   # 'mechanical' | 'deep'
    status: Mapped[str] = mapped_column(String, default="pending")    # pending|running|done|failed
    requested_by: Mapped[str] = mapped_column(String, default="dashboard")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_review_job_store.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/knowledge/models.py tests/test_review_job_store.py
git commit -m "feat(dashboard): ReviewJob model for job pipeline"
```

---

## Task 2: Store `enqueue_job` + `get_job`

**Files:**
- Modify: `src/marshal_core/knowledge/store.py`
- Test: `tests/test_review_job_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_review_job_store.py`:

```python
from marshal_core.knowledge.store import Store


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_review_job_store.py::test_enqueue_and_get_job_roundtrip -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'enqueue_job'`

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/knowledge/store.py`:

First add `ReviewJob` to the models import line at the top. Change:
```python
from .models import InvariantRegistry, GateRun, AuditLog, EscapeRegistry, Meta
```
to:
```python
from .models import InvariantRegistry, GateRun, AuditLog, EscapeRegistry, Meta, ReviewJob
```

Then add these two methods and one helper to the `Store` class (place them after `close_escape`, at the end of the class):

```python
    @staticmethod
    def _job_dict(j: ReviewJob) -> dict:
        return {"id": j.id, "change_ref": j.change_ref, "repo": j.repo,
                "kind": j.kind, "status": j.status, "requested_by": j.requested_by,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                "result": j.result, "error": j.error}

    def enqueue_job(self, change_ref: str, repo: str = "node",
                    kind: str = "mechanical", requested_by: str = "dashboard") -> dict:
        job = ReviewJob(change_ref=change_ref, repo=repo, kind=kind,
                        requested_by=requested_by)
        self.s.add(job)
        self.s.commit()
        return self._job_dict(job)

    def get_job(self, job_id: int) -> dict | None:
        j = self.s.get(ReviewJob, job_id)
        return self._job_dict(j) if j else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_review_job_store.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/knowledge/store.py tests/test_review_job_store.py
git commit -m "feat(dashboard): Store.enqueue_job and get_job"
```

---

## Task 3: Store `claim_next_job` (atomic compare-and-swap)

**Files:**
- Modify: `src/marshal_core/knowledge/store.py`
- Test: `tests/test_review_job_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_review_job_store.py`:

```python
from marshal_core.knowledge.models import ReviewJob as _RJ  # for CAS-guard test


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
    # simulate another worker having grabbed it
    row = db_session.get(_RJ, job["id"])
    row.status = "running"
    db_session.commit()
    assert s.claim_next_job() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_review_job_store.py::test_claim_next_job_returns_oldest_pending_and_marks_running -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'claim_next_job'`

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/knowledge/store.py`, add `update` to the sqlalchemy import at the top. Change:
```python
from sqlalchemy import select, func
```
to:
```python
from sqlalchemy import select, func, update
```

Also add `_now` to the models import so the store can stamp timestamps. Change the models import line to:
```python
from .models import InvariantRegistry, GateRun, AuditLog, EscapeRegistry, Meta, ReviewJob, _now
```

Then add this method to the `Store` class (after `get_job`):

```python
    def claim_next_job(self) -> dict | None:
        # Compare-and-swap claim: read the oldest pending job, then atomically flip
        # it to running guarded by status=='pending'. If another worker won the race
        # (rowcount 0), retry with the next pending row. This is safe on SQLite and
        # guarantees no two workers claim the same job.
        while True:
            job = self.s.scalars(
                select(ReviewJob).where(ReviewJob.status == "pending")
                .order_by(ReviewJob.created_at).limit(1)).first()
            if job is None:
                return None
            res = self.s.execute(
                update(ReviewJob)
                .where(ReviewJob.id == job.id, ReviewJob.status == "pending")
                .values(status="running", started_at=_now()))
            self.s.commit()
            if res.rowcount == 1:
                self.s.refresh(job)
                return self._job_dict(job)
            # lost the race; expire the stale row and try the next pending one
            self.s.expire(job)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_review_job_store.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/knowledge/store.py tests/test_review_job_store.py
git commit -m "feat(dashboard): Store.claim_next_job atomic CAS claim"
```

---

## Task 4: Store `finish_job` + `fail_job`

**Files:**
- Modify: `src/marshal_core/knowledge/store.py`
- Test: `tests/test_review_job_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_review_job_store.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_review_job_store.py::test_finish_job_sets_done_with_result -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'finish_job'`

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/knowledge/store.py`, add these two methods to the `Store` class (after `claim_next_job`):

```python
    def finish_job(self, job_id: int, result: dict) -> dict:
        j = self.s.get(ReviewJob, job_id)
        if j is None:
            raise ValueError(f"job not found: {job_id}")
        j.status = "done"
        j.result = result
        j.finished_at = _now()
        self.s.commit()
        return self._job_dict(j)

    def fail_job(self, job_id: int, error: str) -> dict:
        j = self.s.get(ReviewJob, job_id)
        if j is None:
            raise ValueError(f"job not found: {job_id}")
        j.status = "failed"
        j.error = error
        j.finished_at = _now()
        self.s.commit()
        return self._job_dict(j)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_review_job_store.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/knowledge/store.py tests/test_review_job_store.py
git commit -m "feat(dashboard): Store.finish_job and fail_job"
```

---

## Task 5: `POST /api/jobs` + `GET /api/jobs/{id}` (with localhost token guard)

**Files:**
- Modify: `src/marshal_core/adapters/api.py`
- Test: `tests/test_jobs_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_jobs_api.py`:

```python
import importlib
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "jobs.db"
    monkeypatch.setenv("MARSHAL_DB", f"sqlite:///{db_file}")
    monkeypatch.delenv("MARSHAL_JOB_TOKEN", raising=False)  # dev mode: no token
    import marshal_core.adapters.api as api
    importlib.reload(api)
    return TestClient(api.app)


def test_create_job_returns_pending(client):
    r = client.post("/api/jobs", json={"change_ref": "node#7", "repo": "node"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["kind"] == "mechanical"
    assert body["change_ref"] == "node#7"


def test_get_job_roundtrip_and_404(client):
    job_id = client.post("/api/jobs", json={"change_ref": "node#7"}).json()["id"]
    r = client.get(f"/api/jobs/{job_id}")
    assert r.status_code == 200
    assert r.json()["change_ref"] == "node#7"
    assert client.get("/api/jobs/99999").status_code == 404


def test_create_job_requires_change_ref(client):
    assert client.post("/api/jobs", json={"repo": "node"}).status_code == 422


def test_create_job_rejects_bad_kind(client):
    r = client.post("/api/jobs", json={"change_ref": "x", "kind": "bogus"})
    assert r.status_code == 422


def test_token_guard_blocks_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("MARSHAL_DB", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("MARSHAL_JOB_TOKEN", "secret")
    import marshal_core.adapters.api as api
    importlib.reload(api)
    c = TestClient(api.app)
    # missing token -> 403
    assert c.post("/api/jobs", json={"change_ref": "x"}).status_code == 403
    # correct token -> 200
    ok = c.post("/api/jobs", json={"change_ref": "x"},
                headers={"X-Marshal-Token": "secret"})
    assert ok.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_jobs_api.py -v`
Expected: FAIL (404 on `/api/jobs`, route not defined)

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/adapters/api.py`, extend the fastapi import to include `Header`. Change:
```python
from fastapi import FastAPI, HTTPException, Request
```
to:
```python
from fastapi import FastAPI, Header, HTTPException, Request
```
(`os` is already imported at the top.)

Add these two routes at the END of the file (after the `/` SPA route):

```python
@app.post("/api/jobs")
def api_create_job(body: dict, x_marshal_token: str | None = Header(default=None)):
    expected = os.environ.get("MARSHAL_JOB_TOKEN")
    if expected and x_marshal_token != expected:
        raise HTTPException(status_code=403, detail="invalid or missing token")
    change_ref = body.get("change_ref")
    if not change_ref:
        raise HTTPException(status_code=422, detail="change_ref required")
    kind = body.get("kind", "mechanical")
    if kind not in ("mechanical", "deep"):
        raise HTTPException(status_code=422, detail="kind must be mechanical or deep")
    with _Session() as s:
        return Store(s).enqueue_job(change_ref=change_ref,
                                    repo=body.get("repo", "node"), kind=kind)


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: int):
    with _Session() as s:
        job = Store(s).get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_jobs_api.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/adapters/api.py tests/test_jobs_api.py
git commit -m "feat(dashboard): POST/GET /api/jobs with localhost token guard"
```

---

## Task 6: Worker `run_once` (mechanical handler)

**Files:**
- Create: `src/marshal_core/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_worker.py`:

```python
from marshal_core.knowledge.store import Store
from marshal_pack_cowboy.pack import CowboyPack
from marshal_core.worker import run_once


def test_run_once_no_jobs_returns_false(db_session):
    assert run_once(Store(db_session), CowboyPack()) is False


def test_run_once_mechanical_replans_and_marks_done(db_session):
    s = Store(db_session)
    job = s.enqueue_job(change_ref="abc123", repo="node", kind="mechanical")
    handled = run_once(s, CowboyPack())
    assert handled is True
    done = s.get_job(job["id"])
    assert done["status"] == "done"
    # node repo always carries the econ invariants, so the re-plan is non-empty
    assert done["result"]["count"] >= 1
    assert "econ.fee_conservation" in done["result"]["invariant_ids"]


def test_run_once_deep_job_fails_gracefully_in_phase2(db_session):
    s = Store(db_session)
    job = s.enqueue_job(change_ref="abc123", repo="node", kind="deep")
    assert run_once(s, CowboyPack()) is True
    failed = s.get_job(job["id"])
    assert failed["status"] == "failed"
    assert "Phase 3" in failed["error"]


def test_run_once_records_failure_on_handler_exception(db_session, monkeypatch):
    s = Store(db_session)
    job = s.enqueue_job(change_ref="abc123", repo="node", kind="mechanical")
    # force the mechanical handler to blow up
    import marshal_core.worker as w
    monkeypatch.setattr(w, "_run_mechanical",
                        lambda store, pack, jb: (_ for _ in ()).throw(RuntimeError("kaboom")))
    assert run_once(s, CowboyPack()) is True
    failed = s.get_job(job["id"])
    assert failed["status"] == "failed"
    assert "kaboom" in failed["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marshal_core.worker'`

- [ ] **Step 3: Write minimal implementation**

Create `src/marshal_core/worker.py`:

```python
"""Job worker: claims review_job rows and runs them.

Phase 2 handles only the 'mechanical' kind — it rebuilds a NormalizedEvent and
calls Orchestrator.plan(), which re-selects/registers the applicable invariants.
A mechanical re-plan does NOT produce a gate_run verdict; that is the Phase 3
deep worker's job. 'deep' jobs are failed here with a clear message until then.
"""
import os
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marshal_core.contracts import NormalizedEvent
from marshal_core.knowledge.models import Base
from marshal_core.knowledge.store import Store
from marshal_core.modules.orchestrator import Orchestrator
from marshal_pack_cowboy.pack import CowboyPack


def _run_mechanical(store: Store, pack, job: dict) -> dict:
    event = NormalizedEvent(kind="pr", repo=job["repo"],
                            change_ref=job["change_ref"], diff_paths=[])
    resp = Orchestrator(pack, store).plan(event)
    ids = [i["invariant_id"] for i in resp.invariants]
    return {"invariant_ids": ids, "count": len(ids), "job_id": resp.job_id}


def run_once(store: Store, pack) -> bool:
    """Claim and process at most one job. Returns True if a job was handled."""
    job = store.claim_next_job()
    if job is None:
        return False
    try:
        if job["kind"] == "mechanical":
            result = _run_mechanical(store, pack, job)
            store.finish_job(job["id"], result=result)
        else:
            store.fail_job(job["id"],
                           error="deep review not available until Phase 3")
    except Exception as exc:  # never leave a job stuck 'running'
        store.fail_job(job["id"], error=f"{type(exc).__name__}: {exc}")
    return True


def main() -> None:  # pragma: no cover - thin process loop
    engine = create_engine(os.environ.get("MARSHAL_DB", "sqlite:///marshal.db"))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    pack = CowboyPack()
    poll = float(os.environ.get("MARSHAL_WORKER_POLL_SECONDS", "2"))
    while True:
        with Session() as s:
            handled = run_once(Store(s), pack)
        if not handled:
            time.sleep(poll)


if __name__ == "__main__":  # pragma: no cover
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_worker.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/worker.py tests/test_worker.py
git commit -m "feat(dashboard): worker run_once with mechanical re-plan handler"
```

---

## Task 7: SPA re-review button + live job-status row

**Files:**
- Modify: `src/marshal_core/adapters/static/index.html`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_api.py`:

```python
def test_spa_has_rereview_button_wiring(client):
    html = client.get("/").text
    # the re-review control and its job-polling function must be present
    assert "re-review" in html
    assert "startJob" in html
    assert "/api/jobs" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_dashboard_api.py::test_spa_has_rereview_button_wiring -v`
Expected: FAIL (the SPA has no job wiring yet)

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/adapters/static/index.html`:

**Change 3a — add CSS.** Find this existing rule line:
```
  .muted { color:var(--muted); }
```
Immediately AFTER it, add:
```
  .jobrow { margin-top:10px; font-size:12px; color:var(--muted); display:flex; gap:8px; align-items:center; }
  .btn { background:var(--line); color:var(--fg); border:none; border-radius:6px; padding:4px 10px; font:inherit; font-size:12px; cursor:pointer; }
  .btn:disabled { opacity:.5; cursor:default; }
  .dot { width:8px; height:8px; border-radius:50%; display:inline-block; }
  .dot.pending { background:var(--muted); } .dot.running { background:var(--needs); }
  .dot.done { background:var(--pass); } .dot.failed { background:var(--block); }
```

**Change 3b — add the button + job container to each inbox card.** In `renderInbox()`, find this exact fragment (the end of the card template):
```
      <details><summary>evidence</summary><pre>${esc(JSON.stringify(e,null,2))}</pre></details>
    </div>`);
```
Replace it with:
```
      <details><summary>evidence</summary><pre>${esc(JSON.stringify(e,null,2))}</pre></details>
      <div class="jobrow">
        <button class="btn" onclick="startJob(this, '${esc(r.change_ref)}', '${esc(e.repo||'node')}')">re-review</button>
        <span class="jobstatus"></span>
      </div>
    </div>`);
```

**Change 3c — add the job-driver JS.** Immediately BEFORE this existing line:
```
async function show(tab){
```
insert:
```
async function startJob(btn, changeRef, repo){
  btn.disabled = true;
  const statusEl = btn.parentElement.querySelector('.jobstatus');
  statusEl.innerHTML = '<span class="dot pending"></span> queued…';
  try {
    const r = await fetch('/api/jobs', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({change_ref: changeRef, repo: repo, kind: 'mechanical'})});
    if(!r.ok) throw new Error('enqueue '+r.status);
    const job = await r.json();
    pollJob(job.id, statusEl, btn);
  } catch(e){ statusEl.innerHTML = '<span class="dot failed"></span> '+esc(e.message); btn.disabled=false; }
}

async function pollJob(id, statusEl, btn){
  try {
    const j = await getJSON('/api/jobs/'+id);
    if(j.status==='done'){
      const n = j.result && j.result.count!=null ? j.result.count : '?';
      statusEl.innerHTML = `<span class="dot done"></span> re-planned: ${esc(n)} invariants`;
      btn.disabled = false; return;
    }
    if(j.status==='failed'){
      statusEl.innerHTML = '<span class="dot failed"></span> '+esc(j.error||'failed');
      btn.disabled = false; return;
    }
    statusEl.innerHTML = `<span class="dot ${esc(j.status)}"></span> ${esc(j.status)}…`;
    setTimeout(() => pollJob(id, statusEl, btn), 1500);
  } catch(e){ statusEl.innerHTML = '<span class="dot failed"></span> '+esc(e.message); btn.disabled=false; }
}

```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_dashboard_api.py -v`
Expected: PASS (all prior dashboard_api tests + the new one)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/adapters/static/index.html tests/test_dashboard_api.py
git commit -m "feat(dashboard): re-review button with live job-status polling"
```

---

## Task 8: Full-suite regression + live end-to-end smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest -q`
Expected: all new tests pass; the only failures are the pre-existing `marshal_core/checks/test_system_actor_addrmap.py` pair (external-path dependent — prove pre-existing with `git log <phase2-base>..HEAD -- <file>` returning empty). Note if the flaky `tests/test_cli.py::test_setup_creates_symlink` trips (background-sync race) — it is not a regression.

- [ ] **Step 2: Live end-to-end smoke (API enqueue → worker run_once → done)**

Run:
```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
export MARSHAL_DB="sqlite:///$PWD/marshal_phase2_smoke.db"
venv/bin/python -m uvicorn marshal_core.adapters.api:app --port 8791 >/tmp/p2_smoke.log 2>&1 &
UVPID=$!; sleep 3
echo "--- enqueue ---"; JOB=$(curl -s -X POST localhost:8791/api/jobs -H 'Content-Type: application/json' -d '{"change_ref":"smoke1","repo":"node","kind":"mechanical"}'); echo "$JOB"
JID=$(echo "$JOB" | venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "--- run one worker tick ---"; venv/bin/python -c "
import os; from sqlalchemy import create_engine; from sqlalchemy.orm import sessionmaker
from marshal_core.knowledge.models import Base; from marshal_core.knowledge.store import Store
from marshal_pack_cowboy.pack import CowboyPack; from marshal_core.worker import run_once
e=create_engine(os.environ['MARSHAL_DB']); Base.metadata.create_all(e)
S=sessionmaker(bind=e)
with S() as s: print('handled=', run_once(Store(s), CowboyPack()))
"
echo "--- job status ---"; curl -s localhost:8791/api/jobs/$JID | venv/bin/python -m json.tool
kill $UVPID 2>/dev/null
rm -f marshal_phase2_smoke.db
```
Expected: enqueue returns a `pending` job; the worker tick prints `handled= True`; final job status is `done` with `result.count >= 1` and `econ.fee_conservation` among `result.invariant_ids`.

- [ ] **Step 3: No commit** (verification only). If a fix was required, commit it with a descriptive message.

---

## Self-Review Notes (author)

- **Spec coverage:** §8.2 job abstraction + mechanical re-review → Tasks 1–6; `review_job` state machine (§3, §4.1, generalized `result` JSON) → Tasks 1–4; `POST/GET /api/jobs` localhost+token (§3, §6.3) → Task 5; worker mechanical kind (§6.1 "mechanical: Orchestrator.plan()") → Task 6; SPA "UI designed for job status from day one" (§8.2) + re-review button (§5.1) → Task 7; end-to-end liveness → Task 8. Explicitly deferred: deep worker/worktree/rate-limit/timeout (Phase 3), MTTD (Phase 4) — a `deep` job is failed with a "Phase 3" message, not silently.
- **Placeholder scan:** none — every code step is complete.
- **Type consistency:** `enqueue_job`/`get_job`/`claim_next_job`/`finish_job`/`fail_job` and `_job_dict` are used identically across Store (Tasks 2–4), API (Task 5), and worker (Task 6). Job dict keys (`id, change_ref, repo, kind, status, requested_by, created_at, started_at, finished_at, result, error`) match what the API returns and the SPA reads (`job.id`, `j.status`, `j.result.count`, `j.error`). Worker `run_once(store, pack)` signature matches its call sites in tests and `main()`. `_run_mechanical(store, pack, job)` is the exact monkeypatch target named in Task 6's failure test.
```
