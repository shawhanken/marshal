"""Backfill repo/PR identity onto inbox gate_runs whose evidence lacks it, by asking
GitHub which PR a commit SHA belongs to.

A commit SHA alone doesn't name its repo, so we try each (org, repo) pair that already
appears in some other gate_run's GitHub links (the repo universe this db touches). The
network call is isolated in `fetch_pulls` so tests can stub it; the real call needs
$GITHUB_TOKEN. Results are written back into `evidence["_backfill"]` so the inbox reads
them without repeating the API call.
"""
import json
import os
import re

import httpx
from sqlalchemy import select

from .knowledge.models import GateRun
from .knowledge.store import Store, NEEDS_HUMAN_VERDICTS

_GH_PULL = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/pull/\d+")


def candidate_repos(session) -> list[tuple[str, str]]:
    """(org, repo) pairs seen in any gate_run's GitHub links — the repo universe."""
    pairs: dict[tuple[str, str], None] = {}
    for (ev,) in session.execute(select(GateRun.evidence)):
        if ev is None:
            continue
        for m in _GH_PULL.finditer(json.dumps(ev)):
            pairs[(m.group(1), m.group(2))] = None
    return list(pairs.keys())


def fetch_pulls(org: str, repo: str, sha: str) -> list:
    """Real GitHub call: PRs associated with a commit. [] on any non-200."""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = httpx.get(f"https://api.github.com/repos/{org}/{repo}/commits/{sha}/pulls",
                  headers=headers, timeout=15)
    return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []


def resolve_sha(sha: str, candidates, fetch=fetch_pulls):
    """Return (repo, pr) for the first candidate repo that owns this commit, else None."""
    for org, repo in candidates:
        try:
            pulls = fetch(org, repo, sha)
        except Exception:
            continue
        if pulls:
            num = pulls[0].get("number")
            if num is not None:
                return repo, num
    return None


def resolve_pr(session, change_ref: str, prefer_repo: str | None = None, fetch=None):
    """Return (org, repo, pr) for the PR that owns a commit, else None."""
    if fetch is None:
        fetch = fetch_pulls
    cands = candidate_repos(session)
    if prefer_repo:  # try the job's own repo first
        cands = sorted(cands, key=lambda c: c[1] != prefer_repo)
    for org, repo in cands:
        try:
            pulls = fetch(org, repo, change_ref)
        except Exception:
            continue
        if pulls and pulls[0].get("number") is not None:
            return org, repo, pulls[0]["number"]
    return None


def format_verdict_comment(verdict: dict) -> str:
    """Render a deep-review verdict as a GitHub PR comment (advisory)."""
    lines = [f"## 🤠 Marshal deep review — **{verdict.get('verdict', '?')}**"]
    if verdict.get("summary"):
        lines.append("")
        lines.append(str(verdict["summary"]))
    findings = verdict.get("findings") or []
    if isinstance(findings, list) and findings:
        lines.append("")
        lines.append(f"**Findings ({len(findings)}):**")
        for f in findings[:20]:
            if isinstance(f, dict):
                title = f.get("title") or f.get("location") or ""
                lines.append(f"- **{f.get('severity', '?')}** — {title}")
            else:
                lines.append(f"- {f}")
    lines.append("")
    lines.append("_Posted by the Marshal dashboard deep-review worker — advisory._")
    return "\n".join(lines)


def post_pr_comment(org: str, repo: str, pr, body: str) -> bool:
    """Real GitHub POST of a PR comment. Needs $GITHUB_TOKEN. Returns success."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return False
    r = httpx.post(
        f"https://api.github.com/repos/{org}/{repo}/issues/{pr}/comments",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"},
        json={"body": body}, timeout=15)
    return r.status_code in (200, 201)


def post_deep_verdict(session, change_ref: str, repo: str, verdict: dict,
                      resolver=resolve_pr, poster=post_pr_comment) -> bool:
    """Resolve the PR for change_ref and post the verdict as a comment. Best-effort."""
    found = resolver(session, change_ref, prefer_repo=repo)
    if not found:
        return False
    org, gh_repo, pr = found
    return poster(org, gh_repo, pr, format_verdict_comment(verdict))


def backfill(session, fetch=None, limit: int = 200) -> int:
    """Resolve repo/pr for inbox gate_runs that lack an identity and cache it into
    evidence['_backfill']. Returns the number of rows backfilled."""
    if fetch is None:
        fetch = fetch_pulls
    candidates = candidate_repos(session)
    if not candidates:
        return 0
    rows = session.scalars(
        select(GateRun).where(GateRun.verdict.in_(NEEDS_HUMAN_VERDICTS))
        .order_by(GateRun.id.desc()).limit(limit)).all()
    n = 0
    for gr in rows:
        s = Store.inbox_summary(gr.evidence)
        if s["repo"] and s["pr"] is not None:
            continue  # already identifiable
        found = resolve_sha(gr.change_ref, candidates, fetch=fetch)
        if not found:
            continue
        repo, pr = found
        ev = dict(gr.evidence or {})
        ev["_backfill"] = {"repo": repo, "pr": pr}
        gr.evidence = ev            # reassign so SQLAlchemy tracks the JSON change
        n += 1
    session.commit()
    return n
