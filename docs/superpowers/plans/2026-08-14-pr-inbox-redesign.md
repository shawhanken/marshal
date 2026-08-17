# Inbox Redesign — Repo-First PR Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the DB-first Inbox (a hard-to-read log of `needs_human` gate_runs) with a repo-first work queue: the open PRs across the bound repos, newest-first by `updated_at`, each tagged review-eligible (with re-review / deep-review buttons) or 待处理 (blocked), and annotated with its last local review.

**Architecture:** A new `pr_inbox` module lists open PRs from GitHub (calls isolated behind seams, stubbed in tests, real calls need `$GITHUB_TOKEN`), decides eligibility purely, and joins each PR to its newest local gate_run for the "last review" annotation. `build_inbox` is pure given the seams. The `/api/inbox` endpoint serves a cached snapshot (TTL, since the SPA polls every 15s and each PR costs 1–2 extra API calls). The SPA renders PR cards instead of gate_run cards.

**Tech Stack:** httpx (GitHub), FastAPI, SQLAlchemy, pytest with stubbed seams. Frontend is dependency-free vanilla JS.

**Settled design decisions:**
- Bound repos from `$MARSHAL_REPOS` (default: `cowboyinc/node,cowboyinc/cbfs,cowboyinc/cbss,cowboyinc/cowboy,cowboyinc/runner,shawhanken/marshal`).
- Eligible = open AND not a known merge conflict AND CI not `failure`. **Drafts are eligible.** Ineligible → 待处理 only on a *known* conflict or CI failure (unknown ⇒ eligible, never block on uncertainty).
- Sort by `updated_at` desc.
- Last review per PR from the newest matching gate_run (repo#pr via `Store.inbox_summary`), with a `stale` flag when the PR head moved since.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/marshal_core/pr_inbox.py` | bound repos, GitHub seams, eligibility, review index, `build_inbox` | Create |
| `src/marshal_core/adapters/api.py` | repurpose `GET /api/inbox` to the cached PR queue | Modify |
| `src/marshal_core/adapters/static/index.html` | render PR cards (eligible/待处理, last-review, buttons) | Modify |
| `tests/test_pr_inbox.py` | bound_repos, eligibility, build_inbox (stubbed seams) | Create |
| `tests/test_dashboard_api.py` | update `/api/inbox` tests to the new shape | Modify |

`Store.inbox_summary` / `list_needs_human` stay (inbox_summary is reused to extract repo#pr from gate_runs; list_needs_human is simply no longer wired to the endpoint).

---

## Task 1: `pr_inbox` — bound repos, seams, eligibility

**Files:**
- Create: `src/marshal_core/pr_inbox.py`
- Test: `tests/test_pr_inbox.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pr_inbox.py`:

```python
from marshal_core import pr_inbox


def test_bound_repos_default(monkeypatch):
    monkeypatch.delenv("MARSHAL_REPOS", raising=False)
    repos = pr_inbox.bound_repos()
    assert ("cowboyinc", "node") in repos and ("shawhanken", "marshal") in repos


def test_bound_repos_from_env(monkeypatch):
    monkeypatch.setenv("MARSHAL_REPOS", "acme/foo, acme/bar")
    assert pr_inbox.bound_repos() == [("acme", "foo"), ("acme", "bar")]


def test_eligibility_conflict_and_ci():
    assert pr_inbox.eligibility("dirty", "success") == (False, "merge conflict")
    assert pr_inbox.eligibility("clean", "failure") == (False, "CI failing")
    assert pr_inbox.eligibility("clean", "success") == (True, None)
    assert pr_inbox.eligibility(None, None) == (True, None)        # unknown ⇒ eligible
    assert pr_inbox.eligibility("blocked", "pending") == (True, None)  # not a conflict/failure
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_pr_inbox.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marshal_core.pr_inbox'`

- [ ] **Step 3: Write minimal implementation**

Create `src/marshal_core/pr_inbox.py`:

```python
"""Repo-first PR inbox: open PRs across the bound repos, newest-first, each tagged with
review-eligibility and its last local review. GitHub calls are isolated behind
`list_open_prs`/`pr_detail`/`commit_status` (stubbed in tests; real calls need
$GITHUB_TOKEN). `build_inbox` is pure given those seams.
"""
import os

import httpx
from sqlalchemy import select

from .knowledge.models import GateRun
from .knowledge.store import Store

_DEFAULT_REPOS = ("cowboyinc/node", "cowboyinc/cbfs", "cowboyinc/cbss",
                  "cowboyinc/cowboy", "cowboyinc/runner", "shawhanken/marshal")


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


def commit_status(org: str, repo: str, sha: str):
    if not sha:
        return None
    r = httpx.get(f"https://api.github.com/repos/{org}/{repo}/commits/{sha}/status",
                  headers=_headers(), timeout=15)
    return r.json().get("state") if r.status_code == 200 else None


def eligibility(mergeable_state, ci_state):
    """(eligible, blocked_reason). Ineligible only on a *known* conflict or CI failure."""
    if mergeable_state == "dirty":
        return False, "merge conflict"
    if ci_state == "failure":
        return False, "CI failing"
    return True, None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_pr_inbox.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/pr_inbox.py tests/test_pr_inbox.py
git commit -m "feat(inbox): pr_inbox bound repos, GitHub seams, eligibility"
```

---

## Task 2: `pr_inbox.build_inbox` + review index

**Files:**
- Modify: `src/marshal_core/pr_inbox.py`
- Test: `tests/test_pr_inbox.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pr_inbox.py`:

```python
from marshal_core.knowledge.store import Store


def test_build_inbox_joins_prs_eligibility_and_last_review(db_session, monkeypatch):
    # a prior local review of node#7 at an OLD head -> should show as stale
    s = Store(db_session)
    s.record_gate_run(change_ref="oldhead", job_id="j", verdict="escalate",
                      evidence={"gates": {"repo": "node", "pr": 7}})

    def fake_list(org, repo, per_page=30):
        if repo != "node":
            return []
        return [
            {"number": 7, "title": "fix A", "html_url": "u7", "updated_at": "2026-08-14T02:00:00Z",
             "draft": False, "head": {"sha": "newhead"}},
            {"number": 9, "title": "fix B", "html_url": "u9", "updated_at": "2026-08-14T05:00:00Z",
             "draft": True, "head": {"sha": "h9"}},
        ]
    monkeypatch.setattr(pr_inbox, "list_open_prs", fake_list)
    monkeypatch.setattr(pr_inbox, "pr_detail",
                        lambda o, r, n: {"mergeable_state": "dirty"} if n == 7 else {"mergeable_state": "clean"})
    monkeypatch.setattr(pr_inbox, "commit_status", lambda o, r, sha: "success")

    inbox = pr_inbox.build_inbox(db_session, repos=[("cowboyinc", "node")])
    assert [p["number"] for p in inbox] == [9, 7]          # sorted by updated_at desc
    p9, p7 = inbox[0], inbox[1]
    assert p9["draft"] is True and p9["eligible"] is True   # drafts are eligible
    assert p7["eligible"] is False and p7["blocked_reason"] == "merge conflict"
    assert p7["last_review"] == {"verdict": "escalate", "reviewed_head": "oldhead", "stale": True}
    assert p9["last_review"] is None                        # never reviewed
    assert p7["title"] == "fix A" and p7["url"] == "u7"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_pr_inbox.py::test_build_inbox_joins_prs_eligibility_and_last_review -v`
Expected: FAIL with `AttributeError: module 'marshal_core.pr_inbox' has no attribute 'build_inbox'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/marshal_core/pr_inbox.py`:

```python
def _review_index(session) -> dict:
    """{(repo, str(pr)): {verdict, head_sha}} for the newest gate_run of each PR."""
    idx = {}
    for gr in session.scalars(select(GateRun).order_by(GateRun.id)):
        s = Store.inbox_summary(gr.evidence)
        if s["repo"] and s["pr"] is not None:
            idx[(s["repo"], str(s["pr"]))] = {"verdict": gr.verdict, "head_sha": gr.change_ref}
    return idx  # later (higher-id) rows overwrite earlier ones ⇒ newest wins


def build_inbox(session, repos=None) -> list[dict]:
    repos = repos if repos is not None else bound_repos()
    review_idx = _review_index(session)
    prs = []
    for org, repo in repos:
        for pr in list_open_prs(org, repo):
            num = pr.get("number")
            head_sha = (pr.get("head") or {}).get("sha", "")
            detail = pr_detail(org, repo, num)
            ci = commit_status(org, repo, head_sha)
            eligible, reason = eligibility(detail.get("mergeable_state"), ci)
            last = review_idx.get((repo, str(num)))
            last_review = None
            if last:
                last_review = {"verdict": last["verdict"], "reviewed_head": last["head_sha"],
                               "stale": last["head_sha"] != head_sha}
            prs.append({
                "org": org, "repo": repo, "number": num,
                "title": pr.get("title", ""), "url": pr.get("html_url", ""),
                "head_sha": head_sha, "updated_at": pr.get("updated_at", ""),
                "draft": bool(pr.get("draft")),
                "eligible": eligible, "blocked_reason": reason,
                "last_review": last_review,
            })
    prs.sort(key=lambda p: p["updated_at"], reverse=True)
    return prs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_pr_inbox.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/pr_inbox.py tests/test_pr_inbox.py
git commit -m "feat(inbox): build_inbox joins open PRs with eligibility + last review"
```

---

## Task 3: repurpose `GET /api/inbox` to the cached PR queue

**Files:**
- Modify: `src/marshal_core/adapters/api.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Update the stale `/api/inbox` tests + add the new-shape test**

In `tests/test_dashboard_api.py`, DELETE the old `test_inbox_returns_only_needs_human` (the endpoint no longer returns gate_runs). Then append:

```python
def test_inbox_returns_pr_queue(client, monkeypatch):
    import marshal_core.pr_inbox as pri
    monkeypatch.setenv("MARSHAL_INBOX_TTL_S", "0")   # disable cache for the test
    monkeypatch.setattr(pri, "build_inbox",
                        lambda s, repos=None: [{"repo": "node", "number": 7, "eligible": True}])
    r = client.get("/api/inbox")
    assert r.status_code == 200
    body = r.json()
    assert body["prs"] == [{"repo": "node", "number": 7, "eligible": True}]
    assert "github_token" in body and "repos" in body
```

(If the `client` fixture in this file sets `MARSHAL_DB` but the endpoint now also reads GitHub, the monkeypatched `build_inbox` avoids any real network — good.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_dashboard_api.py::test_inbox_returns_pr_queue -v`
Expected: FAIL — the current `/api/inbox` returns a gate_run list, not `{prs,...}`.

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/adapters/api.py`:

Add near the top imports:
```python
import time
from marshal_core import pr_inbox
```
Add a module-level cache (after `_PACK = CowboyPack()` or near the other module globals):
```python
_pr_cache = {"at": 0.0, "data": None}
```
Replace the existing `/api/inbox` route:
```python
@app.get("/api/inbox")
def api_inbox(limit: int = 50):
    with _Session() as s:
        return Store(s).list_needs_human(limit=limit)
```
with:
```python
@app.get("/api/inbox")
def api_inbox():
    ttl = float(os.environ.get("MARSHAL_INBOX_TTL_S", "90"))
    now = time.monotonic()
    if _pr_cache["data"] is None or now - _pr_cache["at"] > ttl:
        with _Session() as s:
            _pr_cache["data"] = pr_inbox.build_inbox(s)
        _pr_cache["at"] = now
    return {"prs": _pr_cache["data"],
            "github_token": bool(os.environ.get("GITHUB_TOKEN")),
            "repos": [f"{o}/{r}" for o, r in pr_inbox.bound_repos()]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_dashboard_api.py -v`
Expected: PASS (all — incl. the new PR-queue test; the deleted gate-run test is gone)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/adapters/api.py tests/test_dashboard_api.py
git commit -m "feat(inbox): GET /api/inbox serves the cached repo-first PR queue"
```

---

## Task 4: SPA — render PR cards

**Files:**
- Modify: `src/marshal_core/adapters/static/index.html`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_api.py`:

```python
def test_spa_renders_pr_queue(client):
    html = client.get("/").text
    assert "renderInbox" in html
    assert "待处理" in html            # the blocked badge label
    assert "github_token" in html      # SPA reads the token flag for the empty-state hint
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_dashboard_api.py::test_spa_renders_pr_queue -v`
Expected: FAIL (the SPA still renders gate-run cards)

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/adapters/static/index.html`, replace the ENTIRE `renderInbox` function (from `async function renderInbox(){` to its closing `}` before `async function renderHealth`) with:

```javascript
async function renderInbox(){
  const data = await getJSON('/api/inbox');
  const prs = data.prs || [];
  const app = document.getElementById('app');
  if(!prs.length){
    app.innerHTML = data.github_token
      ? '<p class="muted">No open PRs across the bound repos.</p>'
      : '<p class="muted">No PRs — set GITHUB_TOKEN so the dashboard can list open PRs. Bound repos: '+esc((data.repos||[]).join(', '))+'</p>';
    return;
  }
  app.innerHTML = '';
  for(const p of prs){
    const lr = p.last_review;
    const reviewBadge = lr
      ? '<span class="tag">last: '+esc(lr.verdict)+(lr.stale?' · head moved':'')+'</span>'
      : '<span class="muted">not reviewed</span>';
    const meta = [
      (p.head_sha||'').slice(0,8),
      (p.updated_at||'').slice(0,16).replace('T',' '),
      p.draft ? 'draft' : null,
    ].filter(Boolean);
    const card = $(`<div class="card${p.eligible?'':' blocked'}">
      <div class="row" style="align-items:baseline">
        <b class="title">${esc(p.repo)} #${esc(p.number)} — ${esc(p.title)}</b>
        ${p.eligible ? reviewBadge : '<span class="sev-badge">🕗 待处理'+(p.blocked_reason?' · '+esc(p.blocked_reason):'')+'</span>'}
      </div>
      <div class="row">${meta.map(b=>`<span>${esc(b)}</span>`).join('')} ${p.eligible?reviewBadge:''}</div>
      <div class="jobrow">
        <a class="viewlink" href="${esc(p.url)}" target="_blank" rel="noopener">view ↗</a>
        ${p.eligible?'<button class="btn re-plan">re-review</button><button class="btn deep">deep review</button>':''}
        <span class="jobstatus"></span>
      </div>
    </div>`);
    app.appendChild(card);
    if(p.eligible){
      const rePlanBtn = card.querySelector('.re-plan');
      rePlanBtn.addEventListener('click', () => startJob(rePlanBtn, p.head_sha, p.repo, 'mechanical'));
      const deepBtn = card.querySelector('.deep');
      deepBtn.addEventListener('click', () => startJob(deepBtn, p.head_sha, p.repo, 'deep'));
    }
  }
}
```

Then, in the `<style>` block, find the line:
```
  .verdict-needs_human, .verdict-escalate { border-left:3px solid var(--needs); }
```
and add immediately after it:
```
  .card.blocked { opacity:.75; }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_dashboard_api.py -v`
Expected: PASS (all — incl. the new SPA test; `test_spa_has_rereview_button_wiring` / no-inline-onclick / lock-both-buttons still hold since re-review/deep review/startJob/addEventListener/querySelectorAll('.btn') are all still present)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/adapters/static/index.html tests/test_dashboard_api.py
git commit -m "feat(inbox): SPA renders repo-first PR cards (eligible / 待处理 / last review)"
```

---

## Task 5: Full-suite regression + live smoke

**Files:** none (verification only)

- [ ] **Step 1: Full-suite regression**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest -q`
Expected: all new/changed tests pass; the ONLY failures are the pre-existing `marshal_core/checks/test_system_actor_addrmap.py` pair (prove with `git log <base>..HEAD -- <file>` empty). No pr_inbox / dashboard_api test fails.

- [ ] **Step 2: CI-safe endpoint smoke (stubbed GitHub, no token)**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
MARSHAL_INBOX_TTL_S=0 MARSHAL_DB="sqlite:///$PWD/marshal.db" venv/bin/python -c "
import marshal_core.pr_inbox as pri
pri.list_open_prs = lambda o,r,per_page=30: ([{'number':1,'title':'x','html_url':'u','updated_at':'2026-08-14T00:00:00Z','draft':False,'head':{'sha':'abc'}}] if r=='node' else [])
pri.pr_detail = lambda o,r,n: {'mergeable_state':'clean'}
pri.commit_status = lambda o,r,s: 'success'
from sqlalchemy import create_engine; from sqlalchemy.orm import sessionmaker
from marshal_core.knowledge.models import ensure_schema
import os; e=create_engine(os.environ['MARSHAL_DB']); ensure_schema(e)
with sessionmaker(bind=e)() as s:
    inbox = pri.build_inbox(s, repos=[('cowboyinc','node')])
    print('built', len(inbox), 'PR card(s):', [(p['repo']+'#'+str(p['number']), p['eligible']) for p in inbox])
    assert inbox and inbox[0]['eligible']
print('CI-safe inbox smoke OK')
"
```
Expected: prints one eligible PR card and `CI-safe inbox smoke OK`.

- [ ] **Step 3: Live smoke (real GitHub, needs a token) — manual/optional**

This requires a real token + network and is not part of CI. If a token is available:
```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
GITHUB_TOKEN="$(gh auth token)" MARSHAL_INBOX_TTL_S=0 MARSHAL_REPOS="cowboyinc/node" venv/bin/python -c "
import os
from sqlalchemy import create_engine; from sqlalchemy.orm import sessionmaker
from marshal_core.knowledge.models import ensure_schema
from marshal_core import pr_inbox
e=create_engine('sqlite:////home/ubuntu/workspace/marshal/marshal.db'); ensure_schema(e)
with sessionmaker(bind=e)() as s:
    inbox = pr_inbox.build_inbox(s)
    print('open PRs:', len(inbox))
    for p in inbox[:5]:
        lr = p['last_review']
        print(' ', p['repo'], '#'+str(p['number']), '|', ('ELIGIBLE' if p['eligible'] else '待处理:'+str(p['blocked_reason'])),
              '| last:', (lr['verdict']+('(stale)' if lr['stale'] else '')) if lr else 'none', '|', p['title'][:40])
"
```
Expected: lists real open PRs from node with eligibility + last-review annotations.

- [ ] **Step 4: No commit** (verification only).

---

## Self-Review Notes (author)

- **Design coverage:** repo-first from `MARSHAL_REPOS` → Task 1 `bound_repos`; open PRs newest-first by updated_at → Task 2 `build_inbox` sort; eligible = open + no conflict + CI≠failure, drafts eligible → Task 1 `eligibility` + Task 2 join; 待处理 with reason → Tasks 2/4; last review + stale → Task 2 `_review_index`; GitHub dependency + token flag + no-token hint → Task 3 endpoint + Task 4 empty-state; caching for the 15s poll → Task 3 TTL cache; buttons enqueue with head_sha+repo → Task 4.
- **Placeholder scan:** none — every code step is complete.
- **Type consistency:** the PR dict keys (`org, repo, number, title, url, head_sha, updated_at, draft, eligible, blocked_reason, last_review`) are produced in Task 2 `build_inbox` and read identically by the SPA in Task 4; the endpoint envelope `{prs, github_token, repos}` (Task 3) matches what the SPA reads (Task 4). Seam function names `list_open_prs`/`pr_detail`/`commit_status` are defined in Task 1 and monkeypatched by the same names in Tasks 2/3/5. `startJob(btn, changeRef, repo, kind)` is the existing signature — called here with `p.head_sha` as the change_ref.
```
