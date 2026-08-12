"""FastAPI 接入端点。POST /webhook (PR 事件), POST /results (CI 回传)。"""
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.concurrency import run_in_threadpool

from marshal_core.adapters.github import build_check_run, parse_pull_request_event
from marshal_core.contracts import NormalizedEvent, StructuredResult
from marshal_core.knowledge.models import ensure_schema
from marshal_core.knowledge.store import Store
from marshal_core.modules.orchestrator import Orchestrator
from marshal_pack_cowboy.pack import CowboyPack

app = FastAPI(title="Marshal")
_DEFAULT_DB = Path(__file__).resolve().parents[2] / "marshal.db"
_engine = create_engine(os.environ.get("MARSHAL_DB", f"sqlite:///{_DEFAULT_DB}"))
ensure_schema(_engine)
_Session = sessionmaker(bind=_engine)
_PACK = CowboyPack()

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
