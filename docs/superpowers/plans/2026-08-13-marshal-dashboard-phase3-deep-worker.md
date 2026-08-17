# Marshal Dashboard — Phase 3 (Headless-Claude Deep Worker) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a `deep` job actually run a full `/marshal` review by invoking headless Claude (`claude -p`) inside an isolated git worktree, then write the verdict back to a local `gate_run` (no GitHub posting), with worktree isolation, a wall-clock timeout, and every failure path landing the job in `failed` (never `running`).

**Architecture:** The deterministic machinery (worktree lifecycle, verdict parsing, DB write-back, all failure handling) is built and CI-tested. The single un-CI'd concern — the real `claude -p` subprocess running the actual review — is isolated behind `_invoke_claude`, whose *subprocess mechanics* (timeout kill, non-zero exit) are still CI-tested via a fake binary (`MARSHAL_CLAUDE_BIN`); only the real-Claude *semantics* are exercised by an opt-in manual smoke, never CI.

**Tech Stack:** Python `subprocess`, `contextlib.contextmanager`, `json`; pytest with temp git repos + fake `claude` shell scripts (no real LLM in CI). Frontend is dependency-free vanilla JS.

**Scope note:** Phase 3 of the design spec (`docs/superpowers/specs/2026-08-13-marshal-dashboard-design.md`, §8.3 / §6). Settled design decisions: deep review runs the **full `/marshal` skill** (`claude -p`); it is **local-only — the prompt forbids GitHub/external posting**; the verdict is written by Claude to a `MARSHAL_VERDICT.json` file in the worktree; the un-CI'd seam is `_invoke_claude`; default timeout 30 min (`MARSHAL_DEEP_TIMEOUT_S`); worktrees live under a stable base dir, **never `/tmp`** (`MARSHAL_WORKTREE_BASE`, default `~/.marshal/worktrees`). Phase 4 (`introduced_at_ts`/MTTD) remains out of scope.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/marshal_core/worker.py` | `DeepReviewError`, `_parse_verdict`, `_deep_worktree`, `_invoke_claude`, `_deep_prompt`, `_run_deep`; wire `run_once` deep branch | Modify |
| `src/marshal_core/adapters/static/index.html` | "deep review" button (kind=deep) + deep verdict rendering | Modify |
| `scripts/phase3_deep_smoke.sh` | Opt-in manual smoke exercising the real `claude -p` (NOT run in CI) | Create |
| `tests/test_worker_deep.py` | CI tests: parser, worktree lifecycle, subprocess seam (fake bin), `_run_deep`/`run_once` deep paths | Create |
| `tests/test_worker.py` | Update the Phase-2 "deep fails gracefully" test (deep now works) | Modify |
| `tests/test_dashboard_api.py` | Deep-button SPA wiring test | Modify |

---

## Task 1: `_parse_verdict` + `DeepReviewError` + constant

**Files:**
- Modify: `src/marshal_core/worker.py`
- Test: `tests/test_worker_deep.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_worker_deep.py`:

```python
import json
import pytest
from marshal_core.worker import _parse_verdict, DeepReviewError, VERDICT_FILE


def _write(tmp_path, obj):
    p = tmp_path / VERDICT_FILE
    p.write_text(json.dumps(obj))
    return str(p)


def test_parse_verdict_valid(tmp_path):
    path = _write(tmp_path, {"verdict": "needs_human", "summary": "s",
                             "findings": ["f1"], "invariants_run": 5, "invariants_pass": 5})
    v = _parse_verdict(path)
    assert v["verdict"] == "needs_human"
    assert v["findings"] == ["f1"]


def test_parse_verdict_missing_file_raises(tmp_path):
    with pytest.raises(DeepReviewError, match="not written"):
        _parse_verdict(str(tmp_path / VERDICT_FILE))


def test_parse_verdict_bad_json_raises(tmp_path):
    p = tmp_path / VERDICT_FILE
    p.write_text("{not json")
    with pytest.raises(DeepReviewError, match="unparseable"):
        _parse_verdict(str(p))


def test_parse_verdict_invalid_verdict_value_raises(tmp_path):
    path = _write(tmp_path, {"verdict": "lgtm"})
    with pytest.raises(DeepReviewError, match="invalid verdict"):
        _parse_verdict(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_worker_deep.py -v`
Expected: FAIL with `ImportError: cannot import name '_parse_verdict'`

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/worker.py`, add `import json` and `import subprocess` to the top imports (alongside the existing `import os`, `import time`). Add this constant and code after the module docstring / imports, before `_run_mechanical`:

```python
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
    if data.get("verdict") not in ("pass", "needs_human", "block"):
        raise DeepReviewError(f"invalid verdict: {data.get('verdict')!r}")
    return data
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_worker_deep.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/worker.py tests/test_worker_deep.py
git commit -m "feat(dashboard): deep-review verdict parser + DeepReviewError"
```

---

## Task 2: `_deep_worktree` context manager

**Files:**
- Modify: `src/marshal_core/worker.py`
- Test: `tests/test_worker_deep.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_worker_deep.py`:

```python
import os
import subprocess
from marshal_core.worker import _deep_worktree


def _make_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / "f.txt").write_text("hi")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)
    sha = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return sha


def test_deep_worktree_creates_and_tears_down(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    sha = _make_repo(ws / "node")
    monkeypatch.setenv("MARSHAL_WORKSPACE", str(ws))
    monkeypatch.setenv("MARSHAL_WORKTREE_BASE", str(tmp_path / "wts"))

    seen = {}
    with _deep_worktree("node", sha) as wt:
        seen["wt"] = wt
        assert os.path.isdir(wt)
        assert os.path.exists(os.path.join(wt, "f.txt"))  # checked out at the ref
        assert "/tmp/" not in wt                            # stable base, not /tmp
    # torn down after the context exits
    assert not os.path.isdir(seen["wt"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_worker_deep.py::test_deep_worktree_creates_and_tears_down -v`
Expected: FAIL with `ImportError: cannot import name '_deep_worktree'`

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/worker.py`, add `from contextlib import contextmanager` to the imports. Add these helpers after `_parse_verdict`:

```python
def _worktree_base() -> str:
    return os.environ.get("MARSHAL_WORKTREE_BASE",
                          os.path.expanduser("~/.marshal/worktrees"))


@contextmanager
def _deep_worktree(repo: str, change_ref: str):
    # Isolated git worktree of the target repo at change_ref, on a STABLE path
    # (never /tmp — /tmp worktrees get reaped mid-run). Torn down unconditionally.
    workspace = os.environ.get("MARSHAL_WORKSPACE", "/home/ubuntu/workspace")
    repo_root = os.path.join(workspace, repo)
    base = _worktree_base()
    os.makedirs(base, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in f"{repo}-{change_ref}")
    wt = os.path.join(base, safe[:120])
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_worker_deep.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/worker.py tests/test_worker_deep.py
git commit -m "feat(dashboard): isolated git worktree lifecycle for deep review"
```

---

## Task 3: `_invoke_claude` (subprocess seam) + `_deep_timeout`

**Files:**
- Modify: `src/marshal_core/worker.py`
- Test: `tests/test_worker_deep.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_worker_deep.py`:

```python
from marshal_core.worker import _invoke_claude


def _fake_bin(tmp_path, name, body):
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)
    return str(p)


def test_invoke_claude_success_returns_stdout(tmp_path, monkeypatch):
    fake = _fake_bin(tmp_path, "claude_ok.sh", 'echo "did review"; exit 0')
    monkeypatch.setenv("MARSHAL_CLAUDE_BIN", fake)
    out = _invoke_claude("prompt", cwd=str(tmp_path), timeout_s=10)
    assert "did review" in out


def test_invoke_claude_nonzero_exit_raises(tmp_path, monkeypatch):
    fake = _fake_bin(tmp_path, "claude_fail.sh", 'echo "boom" 1>&2; exit 3')
    monkeypatch.setenv("MARSHAL_CLAUDE_BIN", fake)
    with pytest.raises(DeepReviewError, match="exited 3"):
        _invoke_claude("prompt", cwd=str(tmp_path), timeout_s=10)


def test_invoke_claude_timeout_raises(tmp_path, monkeypatch):
    fake = _fake_bin(tmp_path, "claude_hang.sh", 'sleep 5; exit 0')
    monkeypatch.setenv("MARSHAL_CLAUDE_BIN", fake)
    with pytest.raises(subprocess.TimeoutExpired):
        _invoke_claude("prompt", cwd=str(tmp_path), timeout_s=1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_worker_deep.py::test_invoke_claude_success_returns_stdout -v`
Expected: FAIL with `ImportError: cannot import name '_invoke_claude'`

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/worker.py`, add these after `_deep_worktree`:

```python
def _deep_timeout() -> float:
    return float(os.environ.get("MARSHAL_DEEP_TIMEOUT_S", "1800"))  # 30 min default


def _invoke_claude(prompt: str, cwd: str, timeout_s: float) -> str:
    # The ONLY un-CI'd seam: shells out to the real `claude -p`. Subprocess mechanics
    # (timeout kill, non-zero exit) are still CI-tested via a fake MARSHAL_CLAUDE_BIN;
    # only the real-Claude semantics are exercised by the manual smoke.
    binary = os.environ.get("MARSHAL_CLAUDE_BIN", "claude")
    proc = subprocess.run([binary, "-p", prompt], cwd=cwd,
                          capture_output=True, text=True, timeout=timeout_s)
    if proc.returncode != 0:
        raise DeepReviewError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")
    return proc.stdout
```

(Note: `subprocess.run(..., timeout=)` raises `subprocess.TimeoutExpired` and kills the child — we deliberately let that propagate; `_run_deep` maps it to a failed job in Task 4/5.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_worker_deep.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/worker.py tests/test_worker_deep.py
git commit -m "feat(dashboard): _invoke_claude subprocess seam with timeout"
```

---

## Task 4: `_deep_prompt` + `_run_deep` (orchestrate + write-back)

**Files:**
- Modify: `src/marshal_core/worker.py`
- Test: `tests/test_worker_deep.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_worker_deep.py` (reuses `_make_repo` and `_fake_bin` from earlier):

```python
from marshal_core.knowledge.store import Store
from marshal_core.worker import _run_deep


def _deep_env(tmp_path, monkeypatch, verdict_json):
    """A fake claude that writes a verdict file into its cwd (the worktree)."""
    ws = tmp_path / "ws"; ws.mkdir()
    sha = _make_repo(ws / "node")
    monkeypatch.setenv("MARSHAL_WORKSPACE", str(ws))
    monkeypatch.setenv("MARSHAL_WORKTREE_BASE", str(tmp_path / "wts"))
    fake = _fake_bin(tmp_path, "claude_review.sh",
                     f"cat > {VERDICT_FILE} <<'EOF'\n{verdict_json}\nEOF\nexit 0")
    monkeypatch.setenv("MARSHAL_CLAUDE_BIN", fake)
    return sha


def test_run_deep_writes_gate_run_and_finishes(db_session, tmp_path, monkeypatch):
    sha = _deep_env(tmp_path, monkeypatch,
                    '{"verdict":"needs_human","summary":"looks risky",'
                    '"findings":["f1","f2"],"invariants_run":3,"invariants_pass":3}')
    s = Store(db_session)
    job = s.enqueue_job(change_ref=sha, repo="node", kind="deep")
    s.claim_next_job()
    _run_deep(s, job)
    done = s.get_job(job["id"])
    assert done["status"] == "done"
    assert done["result"]["verdict"] == "needs_human"
    gid = done["result"]["gate_run_id"]
    gr = s.get_gate_run(gid)
    assert gr.verdict == "needs_human"
    assert gr.evidence["source"] == "dashboard-worker"
    assert gr.evidence["job_id"] == job["id"]
    assert gr.evidence["findings"] == ["f1", "f2"]


def test_run_deep_propagates_on_bad_verdict(db_session, tmp_path, monkeypatch):
    # fake claude writes an invalid verdict -> _parse_verdict raises -> propagates
    sha = _deep_env(tmp_path, monkeypatch, '{"verdict":"lgtm"}')
    s = Store(db_session)
    job = s.enqueue_job(change_ref=sha, repo="node", kind="deep")
    s.claim_next_job()
    with pytest.raises(DeepReviewError):
        _run_deep(s, job)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_worker_deep.py::test_run_deep_writes_gate_run_and_finishes -v`
Expected: FAIL with `ImportError: cannot import name '_run_deep'`

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/worker.py`, add these after `_invoke_claude`:

```python
def _deep_prompt(job: dict) -> str:
    return (
        f"Run the full /marshal deep review gate on the current git worktree, which is "
        f"checked out at commit {job['change_ref']} of the {job['repo']} repo. "
        f"This is a LOCAL-ONLY dashboard-triggered review: do NOT post anything to GitHub, "
        f"Linear, or any external service. When the review is complete, write your final "
        f"verdict to a file named {VERDICT_FILE} in the current working directory, as JSON "
        f'with keys: "verdict" (one of "pass", "needs_human", "block"; map an escalate to '
        f'"needs_human"), "summary" (string), "findings" (array of strings), '
        f'"invariants_run" (int), "invariants_pass" (int).'
    )


def _run_deep(store: Store, job: dict) -> None:
    with _deep_worktree(job["repo"], job["change_ref"]) as wt:
        _invoke_claude(_deep_prompt(job), cwd=wt, timeout_s=_deep_timeout())
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
```

(The worktree is torn down by the context manager before the DB write — the verdict is already in memory. On any exception inside the `with`, teardown still runs and the exception propagates to `run_once`'s handler, which rolls back and fails the job.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_worker_deep.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/worker.py tests/test_worker_deep.py
git commit -m "feat(dashboard): _run_deep orchestrates worktree+claude+verdict write-back"
```

---

## Task 5: Wire `run_once` deep branch to `_run_deep` (deep now works)

**Files:**
- Modify: `src/marshal_core/worker.py`
- Modify: `tests/test_worker.py`
- Test: `tests/test_worker_deep.py`

- [ ] **Step 1: Write the failing test + update the stale Phase-2 test**

First, in `tests/test_worker.py`, **DELETE** the now-obsolete Phase-2 test `test_run_once_deep_job_fails_gracefully_in_phase2` (deep no longer fails with "Phase 3" — it runs). Remove that whole function.

Then append to `tests/test_worker_deep.py`:

```python
from marshal_pack_cowboy.pack import CowboyPack
from marshal_core.worker import run_once


def test_run_once_deep_success_end_to_end(db_session, tmp_path, monkeypatch):
    sha = _deep_env(tmp_path, monkeypatch,
                    '{"verdict":"pass","summary":"ok","findings":[],'
                    '"invariants_run":2,"invariants_pass":2}')
    s = Store(db_session)
    job = s.enqueue_job(change_ref=sha, repo="node", kind="deep")
    assert run_once(s, CowboyPack()) is True
    done = s.get_job(job["id"])
    assert done["status"] == "done"
    assert done["result"]["verdict"] == "pass"
    assert done["result"]["gate_run_id"] is not None


def test_run_once_deep_timeout_marks_failed(db_session, tmp_path, monkeypatch):
    ws = tmp_path / "ws"; ws.mkdir()
    sha = _make_repo(ws / "node")
    monkeypatch.setenv("MARSHAL_WORKSPACE", str(ws))
    monkeypatch.setenv("MARSHAL_WORKTREE_BASE", str(tmp_path / "wts"))
    hang = _fake_bin(tmp_path, "claude_hang.sh", "sleep 5; exit 0")
    monkeypatch.setenv("MARSHAL_CLAUDE_BIN", hang)
    monkeypatch.setenv("MARSHAL_DEEP_TIMEOUT_S", "1")
    s = Store(db_session)
    job = s.enqueue_job(change_ref=sha, repo="node", kind="deep")
    assert run_once(s, CowboyPack()) is True
    failed = s.get_job(job["id"])
    assert failed["status"] == "failed"          # never left 'running'
    assert "Timeout" in failed["error"] or "timed out" in failed["error"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_worker_deep.py::test_run_once_deep_success_end_to_end -v`
Expected: FAIL — `run_once` still routes deep to `fail_job("... Phase 3")`, so status is `failed`, not `done`.

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/worker.py`, in `run_once`, replace the deep branch. Change:

```python
        if job["kind"] == "mechanical":
            result = _run_mechanical(store, pack, job)
            store.finish_job(job["id"], result=result)
        else:
            store.fail_job(job["id"],
                           error="deep review not available until Phase 3")
```

to:

```python
        if job["kind"] == "mechanical":
            result = _run_mechanical(store, pack, job)
            store.finish_job(job["id"], result=result)
        else:  # deep
            _run_deep(store, job)
```

(`_run_deep` finishes the job itself on success; any exception — worktree failure, `subprocess.TimeoutExpired`, non-zero exit, unparseable verdict — propagates to `run_once`'s existing `except`, which rolls back and calls `fail_job`, so the job never stays `running`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_worker_deep.py tests/test_worker.py -v`
Expected: PASS (test_worker_deep 12 passed; test_worker 5 passed — the 4 remaining Phase-2 tests + none-obsolete; the deleted deep test is gone)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/worker.py tests/test_worker.py tests/test_worker_deep.py
git commit -m "feat(dashboard): run_once routes deep jobs to real _run_deep review"
```

---

## Task 6: SPA "deep review" button + verdict rendering

**Files:**
- Modify: `src/marshal_core/adapters/static/index.html`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_api.py`:

```python
def test_spa_has_deep_review_button(client):
    html = client.get("/").text
    assert "deep review" in html          # the new deep button label
    assert "'deep'" in html                # startJob(..., 'deep') wiring
    # the mechanical re-review button and no-inline-onclick guard still hold
    assert "re-review" in html
    assert 'onclick="startJob' not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_dashboard_api.py::test_spa_has_deep_review_button -v`
Expected: FAIL (no deep button yet)

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/adapters/static/index.html`:

**Change 6a — two buttons in the job row.** Find this exact fragment in `renderInbox`:
```
      <div class="jobrow">
        <button class="btn">re-review</button>
        <span class="jobstatus"></span>
      </div>
```
Replace with:
```
      <div class="jobrow">
        <button class="btn re-plan">re-review</button>
        <button class="btn deep">deep review</button>
        <span class="jobstatus"></span>
      </div>
```

**Change 6b — wire both buttons (raw values, no inline handler).** Find this exact fragment (the listener attach after `app.appendChild(card)`):
```
    app.appendChild(card);
    const jobBtn = card.querySelector('.btn');
    jobBtn.addEventListener('click', () => startJob(jobBtn, r.change_ref, (e.repo || 'node')));
```
Replace with:
```
    app.appendChild(card);
    const rePlanBtn = card.querySelector('.re-plan');
    rePlanBtn.addEventListener('click', () => startJob(rePlanBtn, r.change_ref, (e.repo || 'node'), 'mechanical'));
    const deepBtn = card.querySelector('.deep');
    deepBtn.addEventListener('click', () => startJob(deepBtn, r.change_ref, (e.repo || 'node'), 'deep'));
```

**Change 6c — `startJob` accepts a kind; done-render handles both result shapes.** Find:
```
async function startJob(btn, changeRef, repo){
  btn.disabled = true;
  const statusEl = btn.parentElement.querySelector('.jobstatus');
  statusEl.innerHTML = '<span class="dot pending"></span> queued…';
  try {
    const r = await fetch('/api/jobs', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({change_ref: changeRef, repo: repo, kind: 'mechanical'})});
```
Replace with:
```
async function startJob(btn, changeRef, repo, kind){
  btn.disabled = true;
  const statusEl = btn.parentElement.querySelector('.jobstatus');
  statusEl.innerHTML = '<span class="dot pending"></span> queued…';
  try {
    const r = await fetch('/api/jobs', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({change_ref: changeRef, repo: repo, kind: kind || 'mechanical'})});
```

Then find the `pollJob` done-branch:
```
    if(j.status==='done'){
      const n = j.result && j.result.count!=null ? j.result.count : '?';
      statusEl.innerHTML = `<span class="dot done"></span> re-planned: ${esc(n)} invariants`;
      btn.disabled = false; return;
    }
```
Replace with:
```
    if(j.status==='done'){
      const res = j.result || {};
      let msg;
      if(res.verdict!=null){
        msg = 'deep: ' + esc(res.verdict) + (res.gate_run_id!=null ? ' → run #'+esc(res.gate_run_id) : '');
      } else {
        msg = 're-planned: ' + esc(res.count!=null ? res.count : '?') + ' invariants';
      }
      statusEl.innerHTML = '<span class="dot done"></span> ' + msg;
      btn.disabled = false; return;
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_dashboard_api.py -v`
Expected: PASS (all dashboard_api tests, incl. the new one and the still-passing `test_spa_has_rereview_button_wiring` / `test_rereview_button_has_no_inline_onclick_handler`)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/adapters/static/index.html tests/test_dashboard_api.py
git commit -m "feat(dashboard): deep-review button + verdict status rendering"
```

---

## Task 7: Manual smoke script + full-suite regression

**Files:**
- Create: `scripts/phase3_deep_smoke.sh`
- Verification only otherwise

- [ ] **Step 1: Create the opt-in manual smoke script**

Create `scripts/phase3_deep_smoke.sh` (this is NOT run in CI — it invokes the real `claude`):

```bash
#!/usr/bin/env bash
# Manual, opt-in smoke for the Phase 3 deep worker — exercises the REAL `claude -p`
# running a full /marshal review. NOT part of CI (real LLM cost + minutes).
#
# Usage:  REPO=node CHANGE_REF=<sha> bash scripts/phase3_deep_smoke.sh
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="${REPO:-node}"
CHANGE_REF="${CHANGE_REF:?set CHANGE_REF to a commit SHA present in /home/ubuntu/workspace/$REPO}"
export MARSHAL_DB="sqlite:///$PWD/phase3_smoke.db"
export MARSHAL_DEEP_TIMEOUT_S="${MARSHAL_DEEP_TIMEOUT_S:-1800}"
PY=venv/bin/python

echo "Enqueuing a deep job for $REPO@$CHANGE_REF ..."
$PY -c "
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from marshal_core.knowledge.models import Base
from marshal_core.knowledge.store import Store
from marshal_pack_cowboy.pack import CowboyPack
from marshal_core.worker import run_once
e=create_engine(os.environ['MARSHAL_DB']); Base.metadata.create_all(e); S=sessionmaker(bind=e)
with S() as s:
    st=Store(s); j=st.enqueue_job(change_ref='$CHANGE_REF', repo='$REPO', kind='deep')
    print('enqueued job', j['id'], '- running worker tick (this invokes real claude)...')
    run_once(st, CowboyPack())
    print('final:', st.get_job(j['id']))
"
rm -f phase3_smoke.db
echo "Done. A 'done' status with result.verdict + gate_run_id means the deep path works end to end."
```

Then `chmod +x scripts/phase3_deep_smoke.sh`.

- [ ] **Step 2: Commit the smoke script**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add scripts/phase3_deep_smoke.sh
git commit -m "chore(dashboard): opt-in manual smoke for the real deep-review invocation"
```

- [ ] **Step 3: Full-suite regression**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest -q`
Expected: all new/changed tests pass; the ONLY failures are the pre-existing `marshal_core/checks/test_system_actor_addrmap.py` pair (prove with `git log <phase3-base>..HEAD -- <file>` empty). No Phase 3 test (`test_worker_deep`, `test_worker`, `test_dashboard_api`) fails.

- [ ] **Step 4: CI-safe deep smoke (fake claude, no real LLM)**

Run a fake-binary end-to-end tick to confirm the wiring with no LLM cost:
```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
tmp=$(mktemp -d ./ci_smoke.XXXX)
mkdir -p "$tmp/ws/node" && git -C "$tmp/ws/node" init -q && git -C "$tmp/ws/node" config user.email t@t && git -C "$tmp/ws/node" config user.name t
echo hi > "$tmp/ws/node/f.txt" && git -C "$tmp/ws/node" add . && git -C "$tmp/ws/node" commit -qm init
SHA=$(git -C "$tmp/ws/node" rev-parse HEAD)
printf '#!/bin/sh\ncat > MARSHAL_VERDICT.json <<EOF\n{"verdict":"needs_human","summary":"smoke","findings":["x"],"invariants_run":1,"invariants_pass":1}\nEOF\nexit 0\n' > "$tmp/fake_claude.sh" && chmod +x "$tmp/fake_claude.sh"
MARSHAL_WORKSPACE="$tmp/ws" MARSHAL_WORKTREE_BASE="$tmp/wts" MARSHAL_CLAUDE_BIN="$tmp/fake_claude.sh" MARSHAL_DB="sqlite:///$tmp/s.db" \
  venv/bin/python -c "
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from marshal_core.knowledge.models import Base
from marshal_core.knowledge.store import Store
from marshal_pack_cowboy.pack import CowboyPack
from marshal_core.worker import run_once
e=create_engine(os.environ['MARSHAL_DB']); Base.metadata.create_all(e); S=sessionmaker(bind=e)
with S() as s:
    st=Store(s); j=st.enqueue_job(change_ref='$SHA', repo='node', kind='deep')
    run_once(st, CowboyPack()); jj=st.get_job(j['id'])
    print('status', jj['status'], 'verdict', jj['result']['verdict'], 'gate_run', jj['result']['gate_run_id'])
    assert jj['status']=='done' and jj['result']['verdict']=='needs_human'
"
rm -rf "$tmp"
echo "CI-safe deep smoke OK"
```
Expected: prints `status done verdict needs_human gate_run <id>` and `CI-safe deep smoke OK`. No product-code changes; nothing to commit here.

---

## Self-Review Notes (author)

- **Spec coverage:** §6.1 deep worker runs full /marshal via claude -p → Tasks 3/4 (`_invoke_claude` + `_deep_prompt`); §6.2 worktree isolation on stable path → Task 2 (asserts not-/tmp); rate-limit "one deep at a time" → inherent in the single-process sequential `run_once` (noted; no code needed until mechanical concurrency is added); §6.2 timeout → Task 3 (`_deep_timeout`, TimeoutExpired) + Task 5 timeout→failed test; §6.4 provenance → Task 4 (`evidence.source='dashboard-worker'`+job_id, distinct `deep-{id}` job_id); §6.5 every failure has an exit (never 'running') → Tasks 4/5 (context-manager teardown + run_once rollback+fail_job, timeout test); local-only no-post → Task 4 prompt. The un-CI'd seam is confined to `_invoke_claude`'s real-Claude semantics; its subprocess mechanics are CI-tested via fake bin (Task 3) and the whole `_run_deep`/`run_once` deep path is CI-tested via fake bin (Tasks 4/5).
- **Placeholder scan:** none — every code step is complete.
- **Type consistency:** `_parse_verdict`/`_deep_worktree`/`_invoke_claude`/`_deep_timeout`/`_deep_prompt`/`_run_deep`/`DeepReviewError`/`VERDICT_FILE` are defined in Tasks 1–4 and referenced identically in Tasks 4–5 and the tests. `_run_deep(store, job)` signature matches its call site in `run_once`. The deep result dict `{verdict, gate_run_id}` written by `_run_deep` (Task 4) matches what the SPA `pollJob` reads (Task 6). `record_gate_run(...) -> GateRun` (`.id`, `.verdict`, `.evidence`) matches the Store API and the Task 4 assertions. The deleted Phase-2 test (`test_run_once_deep_job_fails_gracefully_in_phase2`) is removed in Task 5 because deep no longer fails with "Phase 3".
```
