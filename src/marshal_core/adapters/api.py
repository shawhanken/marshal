"""FastAPI 接入端点。POST /webhook (PR 事件), POST /results (CI 回传)。"""
import os
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.concurrency import run_in_threadpool

from marshal_core import pr_inbox
from marshal_core.adapters.github import build_check_run, parse_pull_request_event
from marshal_core.config import db_url
from marshal_core.contracts import NormalizedEvent, StructuredResult
from marshal_core.knowledge.models import ensure_schema
from marshal_core.knowledge.store import Store
from marshal_core.modules.orchestrator import Orchestrator
from marshal_pack_cowboy.pack import CowboyPack

_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Marshal")
_engine = create_engine(db_url())
ensure_schema(_engine)
_Session = sessionmaker(bind=_engine)
_PACK = CowboyPack()
_pr_cache = {"at": 0.0, "data": None}

_EVENTS: dict[str, NormalizedEvent] = {}
_PR_FILES_PAGE_SIZE = 100
_PR_FILES_API_LIMIT = 3000
_HTTP_TIMEOUT_SEC = 30.0


async def _fetch_pr_files(payload: dict) -> list[str]:
    """Fetch a lossless, complete PR file list without blocking the event loop.

    GitHub's files endpoint exposes at most 3,000 files. The webhook payload
    normally contains changed_files; use it as the authoritative count and
    refuse to plan if the endpoint cannot prove that the returned list is
    complete. This prevents a large PR from being silently under-scoped.
    """
    full_name = payload["repository"].get("full_name") or payload["repository"]["name"]
    pr = payload["pull_request"]
    number = pr["number"]
    expected = pr.get("changed_files")
    if not isinstance(expected, int) or expected < 0:
        expected = None
    if expected is not None and expected > _PR_FILES_API_LIMIT:
        raise RuntimeError(
            f"PR reports {expected} changed files, beyond GitHub's "
            f"{_PR_FILES_API_LIMIT}-file API limit")
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    paths: list[str] = []
    page = 1
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        while True:
            response = await client.get(
                f"https://api.github.com/repos/{full_name}/pulls/{number}/files",
                params={"per_page": _PR_FILES_PAGE_SIZE, "page": page},
                headers=headers,
            )
            response.raise_for_status()
            batch = response.json()
            if not isinstance(batch, list):
                raise RuntimeError("GitHub files API returned a non-list payload")
            page_paths = []
            for entry in batch:
                filename = entry.get("filename") if isinstance(entry, dict) else None
                if not isinstance(filename, str):
                    raise RuntimeError("GitHub files API returned an invalid filename")
                page_paths.append(filename)
            paths.extend(page_paths)

            if expected is not None and len(paths) >= expected:
                if len(paths) != expected:
                    raise RuntimeError(
                        f"GitHub files API returned {len(paths)} files, expected {expected}")
                return paths
            if len(batch) < _PR_FILES_PAGE_SIZE:
                if expected is not None and len(paths) != expected:
                    raise RuntimeError(
                        f"GitHub files API returned {len(paths)} files, expected {expected}")
                return paths
            if len(paths) >= _PR_FILES_API_LIMIT:
                raise RuntimeError(
                    "GitHub files API reached its 3,000-file limit without "
                    "proving completeness")
            page += 1


def _handle_event(event: NormalizedEvent):
    with _Session() as s:
        store = Store(s)
        job = Orchestrator(_PACK, store).handle_event(event)
        store.save_planned_event(event, job.job_id)
    return job


def _plan_event(event: NormalizedEvent):
    with _Session() as s:
        store = Store(s)
        resp = Orchestrator(_PACK, store).plan(event)
        store.save_planned_event(event, resp.job_id)
    return resp


def _load_planned_event(job_id: str) -> NormalizedEvent | None:
    with _Session() as s:
        stored = Store(s).get_planned_event(job_id)
    return NormalizedEvent(**stored) if stored is not None else None


def _handle_result(event: NormalizedEvent, result: StructuredResult):
    with _Session() as s:
        decision = Orchestrator(_PACK, Store(s)).handle_result(event, result)
    return decision


@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    if "pull_request" not in payload:
        return {"ignored": True}
    try:
        diff_paths = await _fetch_pr_files(payload)
    except Exception as e:
        raise HTTPException(status_code=502, detail=(
            f"could not fetch the PR's changed files from GitHub ({e}); "
            "refusing to plan against an empty diff"))
    ev = parse_pull_request_event(payload, diff_paths)
    _EVENTS[ev.change_ref] = ev
    job = await run_in_threadpool(_handle_event, ev)
    return {"job_id": job.job_id, "invariant_ids": job.params["invariant_ids"]}


@app.post("/plan")
async def plan(event: NormalizedEvent):
    resp = await run_in_threadpool(_plan_event, event)
    _EVENTS[event.change_ref] = event
    return resp.model_dump()


@app.post("/results")
async def results(result: StructuredResult):
    change_ref = result.job_id.removeprefix("inv-")
    ev = _EVENTS.get(change_ref)
    if ev is None:
        ev = await run_in_threadpool(_load_planned_event, result.job_id)
    if ev is None:
        raise HTTPException(status_code=404, detail=(
            f"unknown job_id {result.job_id!r}: no matching plan; "
            "submit the change via /webhook or /plan first"))
    decision = await run_in_threadpool(_handle_result, ev, result)
    check_run = build_check_run(decision, shadow=True)
    return {"verdict": decision.verdict, "check_run": check_run}


@app.get("/api/inbox")
def api_inbox():
    ttl = float(os.environ.get("MARSHAL_INBOX_TTL_S", "90"))
    now = time.monotonic()
    if _pr_cache["data"] is None or now - _pr_cache["at"] > ttl:
        try:
            with _Session() as s:
                _pr_cache["data"] = pr_inbox.build_inbox(s)
            _pr_cache["at"] = now
        except Exception:
            if _pr_cache["data"] is None:
                _pr_cache["data"] = []   # first build failed -> serve empty, never 500
    return {"prs": _pr_cache["data"],
            "github_token": bool(os.environ.get("GITHUB_TOKEN")),
            "repos": [f"{o}/{r}" for o, r in pr_inbox.bound_repos()]}


@app.get("/api/runs/{run_id}")
def api_run(run_id: int):
    with _Session() as s:
        run = Store(s).get_gate_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="gate_run not found")
        return {"id": run.id, "change_ref": run.change_ref, "job_id": run.job_id,
                "verdict": run.verdict, "evidence": run.evidence,
                "created_at": run.created_at.isoformat()}


@app.get("/api/escapes")
def api_escapes():
    with _Session() as s:
        return Store(s).escape_breakdown()


@app.get("/api/health")
def api_health():
    with _Session() as s:
        st = Store(s)
        payload = st.metrics()
        payload["escape_breakdown"] = st.escape_breakdown()
        payload["invariant_breakdown"] = st.invariant_breakdown()
        payload["verdict_timeseries"] = st.verdict_timeseries()
        return payload


@app.get("/api/gate-runs")
def api_gate_runs(limit: int = 50):
    with _Session() as s:
        return {"runs": Store(s).list_gate_runs(limit=min(max(limit, 1), 200))}


@app.get("/api/invariants")
def api_invariants():
    with _Session() as s:
        return {"invariants": Store(s).invariant_rows()}


@app.get("/api/escape-timeline")
def api_escape_timeline():
    with _Session() as s:
        return {"timeline": Store(s).escape_timeline()}


@app.get("/api/worker")
def api_worker():
    """Worker liveness + queue depth for the dashboard's status strip. `state` is
    down (no fresh heartbeat), idle (alive, nothing running), or busy (a job is
    running). A running job with a stale heartbeat still reads busy, not down."""
    from datetime import datetime, timezone
    with _Session() as s:
        st = Store(s)
        hb = st.get_meta("worker:heartbeat")
        stats = st.job_stats()
    seconds_ago = None
    alive = False
    if hb:
        try:
            seconds_ago = (datetime.now(timezone.utc) - datetime.fromisoformat(hb)).total_seconds()
            alive = seconds_ago < 15
        except ValueError:
            pass
    now = datetime.now(timezone.utc)

    def _elapsed(job):
        # elapsed computed server-side (both UTC) — the stored timestamp is naive UTC, so a
        # browser must NOT Date.parse it as local time (that yields a bogus/negative age)
        if job and job.get("started_at"):
            try:
                started = datetime.fromisoformat(job["started_at"])
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                job["elapsed_s"] = max(0.0, (now - started).total_seconds())
            except ValueError:
                pass
        return job

    current = _elapsed(stats["current"])
    jobs = [_elapsed(j) for j in stats.get("active", [])]
    state = "busy" if current is not None else ("idle" if alive else "down")
    return {"heartbeat": hb, "seconds_ago": seconds_ago, "alive": alive, "state": state,
            "queue": stats["counts"], "current": current, "jobs": jobs}


@app.get("/")
def spa():
    return FileResponse(_STATIC_DIR / "index.html")


def _check_job_token(x_marshal_token: str | None) -> None:
    expected = os.environ.get("MARSHAL_JOB_TOKEN")
    if expected and x_marshal_token != expected:
        raise HTTPException(status_code=403, detail="invalid or missing token")


@app.post("/api/jobs")
def api_create_job(body: dict, x_marshal_token: str | None = Header(default=None)):
    _check_job_token(x_marshal_token)
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
def api_get_job(job_id: int, x_marshal_token: str | None = Header(default=None)):
    _check_job_token(x_marshal_token)
    with _Session() as s:
        job = Store(s).get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job


@app.post("/api/reconcile")
def api_reconcile(body: dict | None = None,
                  x_marshal_token: str | None = Header(default=None)):
    """Reconcile the demand-driven DB registry against the full pack catalog:
    seed the catalog invariants no PR has exercised yet. Dry-run unless
    `{"apply": true}`. Web-triggered, so no --verify here (that needs repo
    checkouts + cargo); the safety rules live in Store.reconcile_invariants."""
    _check_job_token(x_marshal_token)
    apply = bool((body or {}).get("apply", False))
    defs = _PACK.all_invariant_defs()
    with _Session() as s:
        st = Store(s)
        plan = st.reconcile_invariants(defs, apply=apply)
        db_repos = {r["repo"] for r in st.invariant_rows()}
    catalog_repos = {d.location_repo for d in defs}
    bound = {repo for _, repo in pr_inbox.bound_repos()}
    return {"applied": apply,
            "counts": {k: len(v) for k, v in plan.items()},
            "plan": plan,
            "coverage_gaps": sorted(bound - catalog_repos - db_repos)}
