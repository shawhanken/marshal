"""FastAPI 接入端点。POST /webhook (PR 事件), POST /results (CI 回传)。"""
import os
import httpx
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marshal_core.contracts import StructuredResult, NormalizedEvent
from marshal_core.knowledge.models import Base
from marshal_core.knowledge.store import Store
from marshal_core.modules.orchestrator import Orchestrator
from marshal_core.adapters.github import parse_pull_request_event, build_check_run
from marshal_pack_cowboy.pack import CowboyPack

app = FastAPI(title="Marshal")
_engine = create_engine(os.environ.get("MARSHAL_DB", "sqlite:///marshal.db"))
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_PACK = CowboyPack()

_EVENTS: dict[str, NormalizedEvent] = {}


def _fetch_pr_files(payload: dict) -> list[str]:
    full_name = payload["repository"].get("full_name") or payload["repository"]["name"]
    number = payload["pull_request"]["number"]
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    paths: list[str] = []
    page = 1
    while True:
        r = httpx.get(f"https://api.github.com/repos/{full_name}/pulls/{number}/files",
                      params={"per_page": 100, "page": page},
                      headers=headers, timeout=30)
        r.raise_for_status()
        batch = r.json()
        paths.extend(f["filename"] for f in batch)
        if len(batch) < 100:
            return paths
        page += 1


@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    if "pull_request" not in payload:
        return {"ignored": True}
    try:
        diff_paths = _fetch_pr_files(payload)
    except Exception as e:
        raise HTTPException(status_code=502, detail=(
            f"could not fetch the PR's changed files from GitHub ({e}); "
            "refusing to plan against an empty diff"))
    ev = parse_pull_request_event(payload, diff_paths)
    _EVENTS[ev.change_ref] = ev
    with _Session() as s:
        job = Orchestrator(_PACK, Store(s)).handle_event(ev)
    return {"job_id": job.job_id, "invariant_ids": job.params["invariant_ids"]}


@app.post("/plan")
async def plan(event: NormalizedEvent):
    with _Session() as s:
        resp = Orchestrator(_PACK, Store(s)).plan(event)
    _EVENTS[event.change_ref] = event
    return resp.model_dump()


@app.post("/results")
async def results(result: StructuredResult):
    change_ref = result.job_id.removeprefix("inv-")
    ev = _EVENTS.get(change_ref)
    if ev is None:
        raise HTTPException(status_code=404, detail=(
            f"unknown job_id {result.job_id!r}: no matching plan; "
            "submit the change via /webhook or /plan first"))
    with _Session() as s:
        decision = Orchestrator(_PACK, Store(s)).handle_result(ev, result)
    check_run = build_check_run(decision, shadow=True)
    return {"verdict": decision.verdict, "check_run": check_run}
