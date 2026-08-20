"""Repo-first PR inbox: open PRs across the bound repos, newest-first, each tagged with
review-eligibility and its last deep review.

The review-state model mirrors the `marshal-pr-sweep` skill exactly: the authoritative
"already deep-reviewed" signal is the `<!-- marshal-deep sha=<hex> -->` marker that the
sweep embeds in a PR issue comment. sha == current head -> reviewed, no re-review; sha !=
head -> code changed, re-review; no marker -> never deep-reviewed. Only the deep marker
counts (plain `/marshal` mechanical comments do not). The local DB is consulted only to
enrich the displayed verdict for the reviewed sha.

GitHub calls are isolated behind `list_open_prs`/`pr_detail`/`commit_status`/`pr_comments`
(stubbed in tests; real calls need $GITHUB_TOKEN). `build_inbox` is pure given those seams.
"""
import os
import re

import httpx
from sqlalchemy import select

from .knowledge.models import GateRun
from .knowledge.store import Store

_DEFAULT_REPOS = ("cowboyinc/node", "cowboyinc/cbfs", "cowboyinc/cbss",
                  "cowboyinc/cbqs", "cowboyinc/cowboy-protocol", "cowboyinc/gateway",
                  "cowboyinc/cowboy", "cowboyinc/runner", "cowboyinc/store-admin",
                  "cowboyinc/wallet", "shawhanken/marshal")

# The sweep's marker: `<!-- marshal-deep sha=<7..40 hex> -->`. Last match across all
# comments wins (GitHub returns issue comments oldest-first, so the newest marker last).
_MARKER_RE = re.compile(r"marshal-deep\s+sha=([0-9a-f]{7,40})", re.I)

# Standing CIP-10 avoidance (title signal), same as find_targets.sh.
_CIP10_RE = re.compile(r"CIP-?10([^0-9]|$)|Container Registry", re.I)


def bound_repos() -> list[tuple[str, str]]:
    raw = os.environ.get("MARSHAL_REPOS") or ",".join(_DEFAULT_REPOS)
    out = []
    for tok in raw.split(","):
        tok = tok.strip()
        if "/" in tok:
            org, repo = tok.split("/", 1)
            out.append((org.strip(), repo.strip()))
    return out


def _headers() -> dict:
    h = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def list_open_prs(org: str, repo: str, per_page: int = 30) -> list:
    r = httpx.get(f"https://api.github.com/repos/{org}/{repo}/pulls",
                  params={"state": "open", "sort": "updated", "direction": "desc",
                          "per_page": per_page},
                  headers=_headers(), timeout=15)
    return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []


def pr_detail(org: str, repo: str, number) -> dict:
    r = httpx.get(f"https://api.github.com/repos/{org}/{repo}/pulls/{number}",
                  headers=_headers(), timeout=15)
    return r.json() if r.status_code == 200 and isinstance(r.json(), dict) else {}


def pr_comments(org: str, repo: str, number) -> list[str]:
    """Issue-comment bodies for a PR (paginated). Marshal sweep verdicts are issue comments."""
    bodies: list[str] = []
    url = f"https://api.github.com/repos/{org}/{repo}/issues/{number}/comments"
    page = 1
    for _ in range(5):  # up to 500 comments
        r = httpx.get(url, params={"per_page": 100, "page": page},
                      headers=_headers(), timeout=15)
        if r.status_code != 200 or not isinstance(r.json(), list):
            break
        batch = r.json()
        bodies.extend((c.get("body") or "") for c in batch)
        if len(batch) < 100:
            break
        page += 1
    return bodies


def _marshal_deep_marker(bodies: list[str]):
    """Latest `marshal-deep sha=` marker across the comment bodies, or None."""
    found = None
    for b in bodies:
        for m in _MARKER_RE.finditer(b or ""):
            found = m.group(1).lower()
    return found


def _is_cip10(title) -> bool:
    return bool(_CIP10_RE.search(title or ""))


def _ci_from_check_runs(runs: list):
    """Reduce GitHub Actions check-runs to a status string, matching the sweep's rollup
    classification: any FAILURE-class conclusion -> failure; any run not yet completed ->
    pending; otherwise (all success/skipped/neutral, or no runs) -> success/None."""
    if not runs:
        return None
    fail = {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
    saw_fail = saw_pending = False
    for x in runs:
        if x.get("status") != "completed":
            saw_pending = True
        elif (x.get("conclusion") or "").lower() in fail:
            saw_fail = True
    if saw_fail:
        return "failure"
    if saw_pending:
        return "pending"
    return "success"


def commit_status(org: str, repo: str, sha: str):
    if not sha:
        return None
    # GitHub Actions report via check-runs, not the legacy combined-status API.
    cr = httpx.get(f"https://api.github.com/repos/{org}/{repo}/commits/{sha}/check-runs",
                   headers=_headers(), timeout=15)
    if cr.status_code == 200:
        state = _ci_from_check_runs((cr.json() or {}).get("check_runs") or [])
        if state:
            return state
    # fall back to the legacy combined status (older repos / commit statuses)
    st = httpx.get(f"https://api.github.com/repos/{org}/{repo}/commits/{sha}/status",
                   headers=_headers(), timeout=15)
    return st.json().get("state") if st.status_code == 200 else None


def eligibility(mergeable_state, ci_state):
    """(eligible, blocked_reason). A red, in-flight, or conflicted PR is not merge-ready,
    so it is not worth (re-)reviewing — same gate the sweep's find_targets.sh applies."""
    if mergeable_state == "dirty":
        return False, "merge conflict"
    if ci_state == "failure":
        return False, "CI failing"
    if ci_state == "pending":
        return False, "CI pending"
    return True, None


def _db_reviews(session):
    """(per_pr, by_sha) — DB gate_runs used only to enrich the displayed verdict for a
    marker's reviewed sha. `per_pr` maps (repo, str(pr)) -> [(verdict, change_ref), ...] in
    id order; `by_sha` is the flat [(verdict, change_ref), ...] list (many sweep gate-record
    rows carry no repo/pr in evidence, so we can still match them by change_ref == sha)."""
    per_pr: dict = {}
    by_sha: list = []
    for gr in session.scalars(select(GateRun).order_by(GateRun.id)):
        by_sha.append((gr.verdict, gr.change_ref))
        s = Store.inbox_summary(gr.evidence)
        if s["repo"] and s["pr"] is not None:
            per_pr.setdefault((s["repo"], str(s["pr"])), []).append((gr.verdict, gr.change_ref))
    return per_pr, by_sha


def _sha_match(ref, marker_sha) -> bool:
    return bool(ref and marker_sha and (ref.startswith(marker_sha) or marker_sha.startswith(ref)))


def _verdict_for(reviews: list, by_sha: list, marker_sha: str):
    """Verdict at the marker's sha: prefer a same-PR review matching the sha, then the
    newest same-PR review, then any DB row recorded at that sha, else None."""
    for verdict, ref in reversed(reviews):
        if _sha_match(ref, marker_sha):
            return verdict
    if reviews:
        return reviews[-1][0]
    for verdict, ref in reversed(by_sha):
        if _sha_match(ref, marker_sha):
            return verdict
    return None


def build_inbox(session, repos=None) -> list[dict]:
    repos = repos if repos is not None else bound_repos()
    per_pr, by_sha = _db_reviews(session)
    prs = []
    for org, repo in repos:
        for pr in list_open_prs(org, repo):
            num = pr.get("number")
            head_sha = (pr.get("head") or {}).get("sha", "")
            title = pr.get("title", "")
            detail = pr_detail(org, repo, num)
            ci = commit_status(org, repo, head_sha)
            eligible, reason = eligibility(detail.get("mergeable_state"), ci)

            marker = _marshal_deep_marker(pr_comments(org, repo, num))
            reviewed_at_head = bool(marker) and bool(head_sha) and head_sha.startswith(marker)
            last_review = None
            if marker:
                verdict = _verdict_for(per_pr.get((repo, str(num)), []), by_sha, marker)
                last_review = {"verdict": verdict, "reviewed_head": marker,
                               "stale": not reviewed_at_head}

            # Standing CIP-10 avoidance (only overrides an otherwise-eligible PR).
            if eligible and _is_cip10(title):
                eligible, reason = False, "CIP-10 avoidance"
            # Deep-reviewed at the current head, no new commits -> nothing to re-review.
            # (Verdict-independent: a new head always re-opens the PR — sweep sha-state.)
            if eligible and reviewed_at_head:
                vpart = f" {last_review['verdict']}" if last_review and last_review.get("verdict") else ""
                eligible, reason = False, f"reviewed{vpart} · no new commits"

            prs.append({
                "org": org, "repo": repo, "number": num,
                "title": title, "url": pr.get("html_url", ""),
                "head_sha": head_sha, "updated_at": pr.get("updated_at", ""),
                "draft": bool(pr.get("draft")),
                "eligible": eligible, "blocked_reason": reason,
                "ci_state": ci, "mergeable_state": detail.get("mergeable_state"),
                "last_review": last_review,
            })
    prs.sort(key=lambda p: p["updated_at"], reverse=True)
    return prs
