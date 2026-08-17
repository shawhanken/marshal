"""Job worker: claims review_job rows and runs them.

Phase 2 handles only the 'mechanical' kind — it rebuilds a NormalizedEvent and
calls Orchestrator.plan(), which re-selects/registers the applicable invariants.
A mechanical re-plan does NOT produce a gate_run verdict; that is the Phase 3
deep worker's job. 'deep' jobs run a full /marshal review via `claude -p` in an
isolated worktree (see _run_deep) and write the verdict back to a local gate_run.
"""
import json
import os
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marshal_core.config import db_url
from marshal_core.contracts import NormalizedEvent
from marshal_core.knowledge.models import ensure_schema
from marshal_core.knowledge.store import Store
from marshal_core.modules.orchestrator import Orchestrator
from marshal_pack_cowboy.pack import CowboyPack


VERDICT_FILE = "MARSHAL_VERDICT.json"


class DeepReviewError(Exception):
    """Raised when a deep review cannot produce a usable verdict."""


def _parse_verdict(path: str) -> dict:
    if not os.path.exists(path):
        raise DeepReviewError(f"verdict file not written: {path}")
    try:
        with open(path) as fh:
            data = json.loads(fh.read())
    except (ValueError, OSError) as exc:
        raise DeepReviewError(f"verdict file unparseable: {exc}")
    if not isinstance(data, dict):
        raise DeepReviewError(f"verdict file is not a JSON object: {type(data).__name__}")
    if data.get("verdict") not in ("pass", "needs_human", "block"):
        raise DeepReviewError(f"invalid verdict: {data.get('verdict')!r}")
    return data


def _worktree_base() -> str:
    return os.environ.get("MARSHAL_WORKTREE_BASE",
                          os.path.expanduser("~/.marshal/worktrees"))


def _worktree_path(repo: str, change_ref: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in f"{repo}-{change_ref}")
    return os.path.join(_worktree_base(), safe[:120])


@contextmanager
def _deep_worktree(repo: str, change_ref: str):
    # Isolated git worktree of the target repo at change_ref, on a STABLE path
    # (never /tmp — /tmp worktrees get reaped mid-run). Torn down unconditionally.
    workspace = os.environ.get("MARSHAL_WORKSPACE", "/home/ubuntu/workspace")
    workspace_real = os.path.realpath(workspace)
    repo_root = os.path.realpath(os.path.join(workspace, repo))
    # Containment guard: `repo` must resolve to a direct child of the workspace,
    # never escape it (e.g. repo="../evil"). Deep jobs run `claude -p` with full
    # tool access inside repo_root, so an out-of-workspace path is real attack surface.
    if repo_root == workspace_real or os.path.commonpath([repo_root, workspace_real]) != workspace_real:
        raise DeepReviewError(f"repo path escapes workspace: {repo!r}")
    if not os.path.isdir(repo_root):
        raise DeepReviewError(f"no local checkout for repo {repo!r} at {repo_root}")
    os.makedirs(_worktree_base(), exist_ok=True)
    wt = _worktree_path(repo, change_ref)
    # Self-heal a worktree left behind by a hard-crashed prior run: the path is a
    # deterministic function of (repo, change_ref), so a stale dir/registration would
    # otherwise make `worktree add` fail forever for this ref. prune reclaims dropped
    # registrations; force-remove + rmtree clear a lingering directory.
    subprocess.run(["git", "-C", repo_root, "worktree", "prune"],
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", repo_root, "worktree", "remove", "--force", wt],
                   capture_output=True, text=True)
    if os.path.exists(wt):
        shutil.rmtree(wt, ignore_errors=True)
    # PR head commits are often absent from the local checkout — fetch the ref
    # best-effort so `worktree add` can resolve it (no-op if already local / no origin).
    subprocess.run(["git", "-C", repo_root, "fetch", "--quiet", "origin", change_ref],
                   capture_output=True, text=True)
    try:
        subprocess.run(["git", "-C", repo_root, "worktree", "add", "--detach", wt, change_ref],
                       check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise DeepReviewError(f"worktree add failed: {exc.stderr[:300]}")
    try:
        yield wt
    finally:
        subprocess.run(["git", "-C", repo_root, "worktree", "remove", "--force", wt],
                       capture_output=True, text=True)


def _deep_timeout() -> float:
    return float(os.environ.get("MARSHAL_DEEP_TIMEOUT_S", "1800"))  # 30 min default


def _invoke_claude(prompt: str, cwd: str, timeout_s: float) -> str:
    # The ONLY un-CI'd seam: shells out to the real `claude -p`. Subprocess mechanics
    # (timeout kill, non-zero exit) are still CI-tested via a fake MARSHAL_CLAUDE_BIN;
    # only the real-Claude semantics are exercised by the manual smoke.
    binary = os.environ.get("MARSHAL_CLAUDE_BIN", "claude")
    args = [binary, "-p", prompt]
    # Operator-chosen permission mode (e.g. so the headless review can run the skill's
    # tools and write the verdict file). Left unset by default; the operator opts in via
    # $MARSHAL_CLAUDE_PERMISSION_MODE when launching the worker — nothing baked in.
    mode = os.environ.get("MARSHAL_CLAUDE_PERMISSION_MODE")
    if mode:
        args += ["--permission-mode", mode]
    # Scoped tool allowlist (preferred over --dangerously-skip-permissions): lets the
    # headless review run the tools it needs and write the verdict file. Space-separated
    # tool names in $MARSHAL_CLAUDE_ALLOWED_TOOLS, e.g. "Bash Read Write Grep Glob Task".
    allowed = os.environ.get("MARSHAL_CLAUDE_ALLOWED_TOOLS")
    if allowed and allowed.split():
        args += ["--allowedTools", *allowed.split()]
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout_s)
    if proc.returncode != 0:
        raise DeepReviewError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")
    return proc.stdout


def _resolve_pr_number(repo: str, change_ref: str):
    """PR whose HEAD is this commit, so deep review can run in PR mode (full PR diff) like an
    interactive `/marshal deep <repo> <PR#>`. None if the sha isn't a PR head / lookup fails."""
    org = os.environ.get("MARSHAL_GH_ORG", "cowboyinc")
    try:
        from marshal_core.github_backfill import fetch_pulls
        pulls = fetch_pulls(org, repo, change_ref)
    except Exception:
        return None
    for p in pulls:                                   # prefer the PR this commit is the HEAD of
        if (p.get("head") or {}).get("sha") == change_ref:
            return p.get("number")
    return pulls[0].get("number") if pulls else None


def _deep_prompt(job: dict, pr_number=None) -> str:
    budget_min = int(os.environ.get("MARSHAL_DEEP_BUDGET_MIN", "40"))
    max_findings = int(os.environ.get("MARSHAL_DEEP_MAX_FINDINGS", "8"))
    if pr_number is not None:
        target = (
            f"Run `/marshal deep {job['repo']} {pr_number}` — the deep review gate on PR "
            f"#{pr_number} of cowboyinc/{job['repo']}, reviewing the FULL PR diff (gh pr diff), "
            f"not just a single commit.\n"
        )
    else:
        target = (
            f"Run the /marshal deep review gate on the current git worktree, checked out at "
            f"commit {job['change_ref']} of the {job['repo']} repo. Review the DIFF of this commit "
            f"against its parent and that change's immediate blast radius — NOT the whole repository.\n"
        )
    return (
        target +
        f"HARD BUDGET: converge within about {budget_min} minutes. Use a FOCUSED lens set (at "
        f"most 3, the highest-risk for this diff) and cap proven findings at {max_findings}. If "
        f"the change is large, prioritise consensus / econ-conservation / security surfaces and "
        f"prove the highest-priority hypotheses first. As you approach the budget, STOP starting "
        f"new prove agents and emit a verdict NOW, marking any hypotheses you could not finish as "
        f"degraded/uncertain (verdict then at least needs_human). NEVER run unbounded — a timely "
        f"degraded verdict is REQUIRED over a perfect one that never lands.\n"
        f"This is a LOCAL-ONLY dashboard-triggered review: do NOT post anything to GitHub, "
        f"Linear, or any external service. When done, write your final verdict to a file named "
        f"{VERDICT_FILE} in the current working directory, as JSON with keys: \"verdict\" (one of "
        f'"pass", "needs_human", "block"; map an escalate to "needs_human"), "summary" (string), '
        f'"findings" (array of strings), "invariants_run" (int), "invariants_pass" (int).'
    )


def _run_deep(store: Store, job: dict) -> None:
    pr = _resolve_pr_number(job["repo"], job["change_ref"])   # PR mode (full diff) when it's a PR head
    with _deep_worktree(job["repo"], job["change_ref"]) as wt:
        _invoke_claude(_deep_prompt(job, pr), cwd=wt, timeout_s=_deep_timeout())
        verdict = _parse_verdict(os.path.join(wt, VERDICT_FILE))
    gr = store.record_gate_run(
        change_ref=job["change_ref"], job_id=f"deep-{job['id']}",
        verdict=verdict["verdict"],
        evidence={"source": "dashboard-worker", "job_id": job["id"],
                  "summary": verdict.get("summary", ""),
                  "findings": verdict.get("findings", []),
                  "invariants_run": verdict.get("invariants_run"),
                  "invariants_pass": verdict.get("invariants_pass")})
    store.finish_job(job["id"], result={"verdict": verdict["verdict"], "gate_run_id": gr.id})
    # Opt-in: post the verdict to the PR (the skill itself stays local-only; the worker
    # posts deterministically). Off unless $MARSHAL_DEEP_POST is set. Best-effort.
    if os.environ.get("MARSHAL_DEEP_POST"):
        try:
            from marshal_core.github_backfill import post_deep_verdict
            post_deep_verdict(store.s, job["change_ref"], job["repo"], verdict)
        except Exception:
            pass


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
        else:  # deep
            _run_deep(store, job)
    except Exception as exc:  # never leave a job stuck 'running'
        # If the handler failed mid-commit the session is in a pending-rollback
        # state; clear it so fail_job can persist the failure on the same session.
        store.s.rollback()
        store.fail_job(job["id"], error=f"{type(exc).__name__}: {exc}")
    return True


def maybe_backfill(session) -> int:
    """Auto-fill repo/PR identity for inbox rows that lack it (new escalate reviews).
    No-op unless $GITHUB_TOKEN is set; never raises — enrichment must not crash the
    worker or block job processing."""
    if not os.environ.get("GITHUB_TOKEN"):
        return 0
    try:
        from marshal_core.github_backfill import backfill
        return backfill(session, limit=int(os.environ.get("MARSHAL_BACKFILL_LIMIT", "50")))
    except Exception:
        return 0


def _heartbeat_loop(Session, stop, every: float = 5.0) -> None:  # pragma: no cover - thread
    """Write a liveness timestamp every few seconds, on its own connection, so the
    dashboard can tell a running worker from a dead one even while a long deep job
    blocks the main loop. 'busy vs idle' is derived from the running-job row, not this."""
    while not stop.is_set():
        try:
            with Session() as s:
                Store(s).set_meta("worker:heartbeat", datetime.now(timezone.utc).isoformat())
        except Exception:
            pass
        stop.wait(every)


def main() -> None:  # pragma: no cover - thin process loop
    engine = create_engine(db_url())
    ensure_schema(engine)
    Session = sessionmaker(bind=engine)
    pack = CowboyPack()
    poll = float(os.environ.get("MARSHAL_WORKER_POLL_SECONDS", "2"))
    backfill_every = float(os.environ.get("MARSHAL_BACKFILL_INTERVAL_S", "300"))
    with Session() as s:
        Store(s).reclaim_stale_jobs()   # clear orphans left by a previously-killed worker
    stop = threading.Event()
    threading.Thread(target=_heartbeat_loop, args=(Session, stop), daemon=True).start()
    last_backfill = 0.0
    while True:
        with Session() as s:
            handled = run_once(Store(s), pack)
            if time.monotonic() - last_backfill >= backfill_every:
                maybe_backfill(s)
                Store(s).reclaim_stale_jobs()
                last_backfill = time.monotonic()
        if not handled:
            time.sleep(poll)


if __name__ == "__main__":  # pragma: no cover
    main()
