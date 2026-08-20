# Marshal Dashboard — Phase 1 (Read-Only Board) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live read-only dashboard (Inbox + Health screens) to the existing `marshal_core` FastAPI service, backed by the existing `marshal.db`.

**Architecture:** New read-only GET endpoints on the *existing* FastAPI app (`src/marshal_core/adapters/api.py`), each a pure projection of new aggregate methods on the existing `Store`. A single self-contained SPA (vanilla JS + inline SVG charts, no CDN) served at `GET /`. No new tables, no new process — the board reads the same DB the orchestrator writes to, so it is live by construction.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, pydantic, pytest + FastAPI `TestClient`. Frontend is dependency-free vanilla JS.

**Scope note:** This is Phase 1 of the design spec (`docs/superpowers/specs/2026-08-13-marshal-dashboard-design.md`, §8.1). The `review_job` table, re-review button, headless-Claude worker (§8.2–8.3), and `introduced_at_ts`/MTTD (§8.4) are **out of scope** here and get their own plans. The Health screen therefore omits the MTTD block and honestly labels it as pending, matching the existing `metrics().unavailable` pattern.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/marshal_core/knowledge/store.py` | Add read-only aggregate methods used by the board | Modify |
| `src/marshal_core/adapters/api.py` | Add GET endpoints + serve the SPA | Modify |
| `src/marshal_core/adapters/static/index.html` | The self-contained SPA (markup + CSS + JS) | Create |
| `tests/test_dashboard_store.py` | Unit tests for the new Store aggregates | Create |
| `tests/test_dashboard_api.py` | API-contract tests via `TestClient` | Create |

All new Store methods return plain JSON-serializable Python (dicts/lists/primitives) so endpoints can return them directly.

---

## Task 1: `Store.list_needs_human()` — the inbox query

**Files:**
- Modify: `src/marshal_core/knowledge/store.py`
- Test: `tests/test_dashboard_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_store.py`:

```python
from marshal_core.knowledge.store import Store


def _seed_runs(s):
    s.record_gate_run(change_ref="node#1", job_id="j1", verdict="pass", evidence={})
    s.record_gate_run(change_ref="node#2", job_id="j2", verdict="needs_human",
                      evidence={"pr": 2, "repo": "node", "tier": "high", "cip": "CIP-3",
                                "dimensions": ["econ"], "invariants_run": 5,
                                "invariants_pass": 5, "high_sev_findings": 0,
                                "advisory_findings": ["a1"]})
    s.record_gate_run(change_ref="node#3", job_id="j3", verdict="needs_human",
                      evidence={"pr": 3, "repo": "runner", "tier": "mid"})
    s.record_gate_run(change_ref="node#4", job_id="j4", verdict="block", evidence={})


def test_list_needs_human_returns_only_needs_human_newest_first(db_session):
    s = Store(db_session)
    _seed_runs(s)
    rows = s.list_needs_human()
    assert [r["change_ref"] for r in rows] == ["node#3", "node#2"]
    assert rows[0]["id"] is not None
    assert rows[0]["verdict"] == "needs_human"
    assert rows[1]["evidence"]["tier"] == "high"


def test_list_needs_human_respects_limit(db_session):
    s = Store(db_session)
    _seed_runs(s)
    assert len(s.list_needs_human(limit=1)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && python -m pytest tests/test_dashboard_store.py -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'list_needs_human'`

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/knowledge/store.py`, add this method to the `Store` class (place it after `get_gate_run`):

```python
    def list_needs_human(self, limit: int = 50) -> list[dict]:
        stmt = (select(GateRun)
                .where(GateRun.verdict == "needs_human")
                .order_by(GateRun.id.desc())
                .limit(limit))
        return [
            {"id": r.id, "change_ref": r.change_ref, "job_id": r.job_id,
             "verdict": r.verdict, "evidence": r.evidence,
             "created_at": r.created_at.isoformat()}
            for r in self.s.scalars(stmt)
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && python -m pytest tests/test_dashboard_store.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/knowledge/store.py tests/test_dashboard_store.py
git commit -m "feat(dashboard): Store.list_needs_human for inbox queue"
```

---

## Task 2: `Store.escape_breakdown()` — ratchet by root cause

**Files:**
- Modify: `src/marshal_core/knowledge/store.py`
- Test: `tests/test_dashboard_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_store.py`:

```python
def test_escape_breakdown_groups_by_root_cause_all_statuses(db_session):
    s = Store(db_session)
    s.open_escape(id="e1", description="d", root_cause_class="econ-conservation")
    s.open_escape(id="e2", description="d", root_cause_class="econ-conservation")
    s.open_escape(id="e3", description="d", root_cause_class="state-consensus")
    s.close_escape("e2", spawned_check="i.x")
    rows = s.escape_breakdown()
    by_class = {r["root_cause_class"]: r for r in rows}
    assert by_class["econ-conservation"]["count"] == 2
    assert by_class["econ-conservation"]["open"] == 1
    assert by_class["econ-conservation"]["closed"] == 1
    assert by_class["state-consensus"]["count"] == 1
    # sorted by count desc so the worst-offending class is first
    assert rows[0]["count"] >= rows[-1]["count"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && python -m pytest tests/test_dashboard_store.py::test_escape_breakdown_groups_by_root_cause_all_statuses -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'escape_breakdown'`

- [ ] **Step 3: Write minimal implementation**

In `store.py`, add to the `Store` class (after `list_open_escapes`):

```python
    def escape_breakdown(self) -> list[dict]:
        stmt = (select(EscapeRegistry.root_cause_class, EscapeRegistry.status,
                       func.count())
                .group_by(EscapeRegistry.root_cause_class, EscapeRegistry.status))
        agg: dict[str, dict] = {}
        for root_cause, status, n in self.s.execute(stmt):
            slot = agg.setdefault(
                root_cause, {"root_cause_class": root_cause, "count": 0,
                             "open": 0, "closed": 0})
            slot["count"] += n
            if status in ("open", "closed"):
                slot[status] += n
        return sorted(agg.values(), key=lambda r: r["count"], reverse=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && python -m pytest tests/test_dashboard_store.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/knowledge/store.py tests/test_dashboard_store.py
git commit -m "feat(dashboard): Store.escape_breakdown groups ratchet by root cause"
```

---

## Task 3: `Store.invariant_breakdown()` — coverage & candidate-red gaps

**Files:**
- Modify: `src/marshal_core/knowledge/store.py`
- Test: `tests/test_dashboard_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_store.py`:

```python
def test_invariant_breakdown_counts_status_severity_and_lists_candidate_red(db_session):
    s = Store(db_session)
    common = dict(domain_pack="cowboy", domain="econ", executor_kind="test",
                  location_repo="node", location_path="p", location_test="t")
    s.register_invariant(id="i.a", severity="high", status="active", **common)
    s.register_invariant(id="i.b", severity="high", status="active", **common)
    s.register_invariant(id="i.c", severity="medium", status="active", **common)
    s.register_invariant(id="i.red", severity="high", status="candidate-red", **common)
    b = s.invariant_breakdown()
    assert b["by_status"]["active"] == 3
    assert b["by_status"]["candidate-red"] == 1
    assert b["by_severity"]["high"] == 3
    assert b["by_severity"]["medium"] == 1
    assert b["candidate_red_ids"] == ["i.red"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && python -m pytest tests/test_dashboard_store.py::test_invariant_breakdown_counts_status_severity_and_lists_candidate_red -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'invariant_breakdown'`

- [ ] **Step 3: Write minimal implementation**

In `store.py`, add to the `Store` class (after `list_invariants`):

```python
    def invariant_breakdown(self) -> dict:
        by_status: dict[str, int] = {}
        for status, n in self.s.execute(
                select(InvariantRegistry.status, func.count())
                .group_by(InvariantRegistry.status)):
            by_status[status] = n
        by_severity: dict[str, int] = {}
        for sev, n in self.s.execute(
                select(InvariantRegistry.severity, func.count())
                .group_by(InvariantRegistry.severity)):
            by_severity[sev] = n
        candidate_red_ids = list(self.s.scalars(
            select(InvariantRegistry.id)
            .where(InvariantRegistry.status == "candidate-red")
            .order_by(InvariantRegistry.id)))
        return {"by_status": by_status, "by_severity": by_severity,
                "candidate_red_ids": candidate_red_ids}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && python -m pytest tests/test_dashboard_store.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/knowledge/store.py tests/test_dashboard_store.py
git commit -m "feat(dashboard): Store.invariant_breakdown for coverage view"
```

---

## Task 4: `Store.verdict_timeseries()` — "is the gate getting better" trend

**Files:**
- Modify: `src/marshal_core/knowledge/store.py`
- Test: `tests/test_dashboard_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_store.py`:

```python
from datetime import datetime, timezone
from marshal_core.knowledge.models import GateRun


def test_verdict_timeseries_buckets_by_day(db_session):
    s = Store(db_session)
    # insert runs on two distinct days with explicit created_at
    db_session.add(GateRun(change_ref="a", job_id="a", verdict="pass", evidence={},
                           created_at=datetime(2026, 6, 1, 10, tzinfo=timezone.utc)))
    db_session.add(GateRun(change_ref="b", job_id="b", verdict="needs_human", evidence={},
                           created_at=datetime(2026, 6, 1, 12, tzinfo=timezone.utc)))
    db_session.add(GateRun(change_ref="c", job_id="c", verdict="pass", evidence={},
                           created_at=datetime(2026, 6, 2, 9, tzinfo=timezone.utc)))
    db_session.commit()
    ts = s.verdict_timeseries()
    assert ts == [
        {"date": "2026-06-01", "pass": 1, "needs_human": 1, "block": 0},
        {"date": "2026-06-02", "pass": 1, "needs_human": 0, "block": 0},
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && python -m pytest tests/test_dashboard_store.py::test_verdict_timeseries_buckets_by_day -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'verdict_timeseries'`

- [ ] **Step 3: Write minimal implementation**

In `store.py`, add to the `Store` class (after `metrics`):

```python
    def verdict_timeseries(self) -> list[dict]:
        # Bucket gate runs by calendar day (UTC) and verdict. Done in Python so it
        # stays engine-agnostic (SQLite date() vs Postgres date_trunc differ).
        buckets: dict[str, dict] = {}
        for r in self.s.scalars(select(GateRun).order_by(GateRun.created_at)):
            day = r.created_at.date().isoformat()
            slot = buckets.setdefault(
                day, {"date": day, "pass": 0, "needs_human": 0, "block": 0})
            if r.verdict in slot:
                slot[r.verdict] += 1
        return [buckets[d] for d in sorted(buckets)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && python -m pytest tests/test_dashboard_store.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/knowledge/store.py tests/test_dashboard_store.py
git commit -m "feat(dashboard): Store.verdict_timeseries for trend chart"
```

---

## Task 5: `GET /api/inbox` and `GET /api/runs/{id}` endpoints

**Files:**
- Modify: `src/marshal_core/adapters/api.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_api.py`:

```python
import importlib
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "dash.db"
    monkeypatch.setenv("MARSHAL_DB", f"sqlite:///{db_file}")
    import marshal_core.adapters.api as api
    importlib.reload(api)
    # seed via the same engine the app now uses
    from marshal_core.knowledge.store import Store
    with api._Session() as s:
        st = Store(s)
        st.record_gate_run(change_ref="node#2", job_id="j2", verdict="needs_human",
                           evidence={"pr": 2, "repo": "node", "tier": "high"})
        st.record_gate_run(change_ref="node#9", job_id="j9", verdict="pass", evidence={})
    return TestClient(api.app)


def test_inbox_returns_only_needs_human(client):
    r = client.get("/api/inbox")
    assert r.status_code == 200
    body = r.json()
    assert [row["change_ref"] for row in body] == ["node#2"]
    assert body[0]["evidence"]["tier"] == "high"


def test_runs_endpoint_returns_evidence(client):
    run_id = client.get("/api/inbox").json()[0]["id"]
    r = client.get(f"/api/runs/{run_id}")
    assert r.status_code == 200
    assert r.json()["change_ref"] == "node#2"


def test_runs_endpoint_404_on_missing(client):
    r = client.get("/api/runs/99999")
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && python -m pytest tests/test_dashboard_api.py -v`
Expected: FAIL with 404 on `/api/inbox` (route not defined)

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/adapters/api.py`, add at the top with the other imports:

```python
from fastapi import HTTPException
from marshal_core.knowledge.store import Store
```

Then add these routes at the end of the file:

```python
@app.get("/api/inbox")
def api_inbox(limit: int = 50):
    with _Session() as s:
        return Store(s).list_needs_human(limit=limit)


@app.get("/api/runs/{run_id}")
def api_run(run_id: int):
    with _Session() as s:
        run = Store(s).get_gate_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="gate_run not found")
        return {"id": run.id, "change_ref": run.change_ref, "job_id": run.job_id,
                "verdict": run.verdict, "evidence": run.evidence,
                "created_at": run.created_at.isoformat()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && python -m pytest tests/test_dashboard_api.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/adapters/api.py tests/test_dashboard_api.py
git commit -m "feat(dashboard): GET /api/inbox and /api/runs/{id}"
```

---

## Task 6: `GET /api/escapes` and `GET /api/health` endpoints

**Files:**
- Modify: `src/marshal_core/adapters/api.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_api.py`:

```python
def test_health_composes_metrics_and_breakdowns(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    # verdict distribution comes straight from metrics()
    assert body["gate_runs_by_verdict"]["needs_human"] == 1
    assert body["gate_runs_by_verdict"]["pass"] == 1
    # new aggregate blocks are present
    assert "escape_breakdown" in body
    assert "invariant_breakdown" in body
    assert "verdict_timeseries" in body
    # honest gaps carried through, including MTTD still pending in Phase 1
    assert "mean_time_to_detection" in body["unavailable"]


def test_escapes_endpoint_returns_breakdown(client):
    with_escape = client.get("/api/escapes")
    assert with_escape.status_code == 200
    assert isinstance(with_escape.json(), list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && python -m pytest tests/test_dashboard_api.py::test_health_composes_metrics_and_breakdowns -v`
Expected: FAIL with 404 (route not defined)

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/adapters/api.py`, add these routes at the end of the file:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && python -m pytest tests/test_dashboard_api.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/adapters/api.py tests/test_dashboard_api.py
git commit -m "feat(dashboard): GET /api/health and /api/escapes"
```

---

## Task 7: Serve the SPA at `GET /`

**Files:**
- Create: `src/marshal_core/adapters/static/index.html`
- Modify: `src/marshal_core/adapters/api.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_api.py`:

```python
def test_root_serves_spa(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Marshal" in r.text
    assert 'id="app"' in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && python -m pytest tests/test_dashboard_api.py::test_root_serves_spa -v`
Expected: FAIL (either 404, or existing default returns non-HTML)

- [ ] **Step 3a: Create the SPA file**

Create `src/marshal_core/adapters/static/index.html` with this exact content:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Marshal Dashboard</title>
<style>
  :root { --bg:#0f1115; --card:#1a1d24; --fg:#e6e6e6; --muted:#9aa4b2;
          --pass:#3fb950; --needs:#d29922; --block:#f85149; --red:#f85149;
          --line:#2d323c; }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f6f7f9; --card:#fff; --fg:#1a1d24; --muted:#5a6472; --line:#e2e5ea; }
  }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.5 system-ui,sans-serif; background:var(--bg); color:var(--fg); }
  header { padding:16px 20px; border-bottom:1px solid var(--line); display:flex; gap:16px; align-items:center; }
  header h1 { font-size:16px; margin:0; }
  nav button { background:none; border:none; color:var(--muted); font:inherit; cursor:pointer; padding:6px 10px; border-radius:6px; }
  nav button.active { background:var(--card); color:var(--fg); }
  main { padding:20px; max-width:960px; margin:0 auto; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin-bottom:12px; }
  .card h3 { margin:0 0 8px; font-size:13px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
  .row { display:flex; flex-wrap:wrap; gap:10px 18px; color:var(--muted); font-size:13px; }
  .tag { display:inline-block; padding:1px 7px; border-radius:6px; background:var(--line); color:var(--fg); font-size:12px; }
  .tier-high { color:var(--block); font-weight:600; }
  .verdict-needs_human { border-left:3px solid var(--needs); }
  details summary { cursor:pointer; color:var(--muted); }
  pre { overflow:auto; background:var(--bg); padding:10px; border-radius:6px; font-size:12px; }
  .bar { height:16px; border-radius:4px; background:var(--needs); }
  .barlabel { display:flex; justify-content:space-between; font-size:12px; margin:6px 0 2px; }
  .gap { color:var(--muted); font-size:12px; border-top:1px dashed var(--line); padding-top:8px; margin-top:8px; }
  .muted { color:var(--muted); }
</style>
</head>
<body>
<header>
  <h1>🤠 Marshal</h1>
  <nav>
    <button id="tab-inbox" class="active" onclick="show('inbox')">Inbox</button>
    <button id="tab-health" onclick="show('health')">Health</button>
  </nav>
  <span id="status" class="muted" style="margin-left:auto"></span>
</header>
<main id="app"><p class="muted">Loading…</p></main>
<script>
const $ = (h) => { const t=document.createElement('template'); t.innerHTML=h.trim(); return t.content.firstChild; };
const esc = (s) => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let current = 'inbox';

async function getJSON(u){ const r=await fetch(u); if(!r.ok) throw new Error(u+' '+r.status); return r.json(); }

function bar(label, value, max){
  const pct = max ? Math.round(100*value/max) : 0;
  return `<div class="barlabel"><span>${esc(label)}</span><span>${value}</span></div>
          <div class="bar" style="width:${pct}%"></div>`;
}

async function renderInbox(){
  const rows = await getJSON('/api/inbox');
  const app = document.getElementById('app');
  if(!rows.length){ app.innerHTML='<p class="muted">Inbox clear — nothing awaiting human review.</p>'; return; }
  app.innerHTML = '';
  for(const r of rows){
    const e = r.evidence || {};
    const tierClass = e.tier==='high' ? 'tier-high' : '';
    const card = $(`<div class="card verdict-${esc(r.verdict)}">
      <div class="row">
        <b>${esc(r.change_ref)}</b>
        ${e.cip?`<span class="tag">${esc(e.cip)}</span>`:''}
        <span class="${tierClass}">tier: ${esc(e.tier||'?')}</span>
        <span class="muted">${esc((r.created_at||'').slice(0,16).replace('T',' '))}</span>
      </div>
      <div class="row">
        ${e.dimensions?`<span>dimensions: ${esc((e.dimensions||[]).join(' '))}</span>`:''}
        ${e.invariants_run!=null?`<span>invariants: ${e.invariants_pass}/${e.invariants_run}</span>`:''}
        ${e.high_sev_findings!=null?`<span>high-sev: ${e.high_sev_findings}</span>`:''}
        ${(e.advisory_findings||[]).length?`<span>▸ ${e.advisory_findings.length} advisory</span>`:''}
      </div>
      <details><summary>evidence</summary><pre>${esc(JSON.stringify(e,null,2))}</pre></details>
    </div>`);
    app.appendChild(card);
  }
}

async function renderHealth(){
  const h = await getJSON('/api/health');
  const app = document.getElementById('app');
  const v = h.gate_runs_by_verdict || {};
  const vmax = Math.max(1, ...Object.values(v));
  const eb = h.escape_breakdown || [];
  const ebmax = Math.max(1, ...eb.map(x=>x.count));
  const ib = h.invariant_breakdown || {by_status:{},by_severity:{},candidate_red_ids:[]};
  const gaps = h.unavailable || {};
  app.innerHTML = `
    <div class="card"><h3>Gate output</h3>
      ${bar('pass', v.pass||0, vmax)}${bar('needs_human', v.needs_human||0, vmax)}${bar('block', v.block||0, vmax)}
      <div class="row" style="margin-top:8px"><span class="muted">total runs: ${h.gate_runs_total||0}</span></div>
    </div>
    <div class="card"><h3>Ratchet — escapes by root cause</h3>
      ${eb.map(x=>bar(x.root_cause_class+`  (${x.open} open / ${x.closed} closed)`, x.count, ebmax)).join('') || '<span class="muted">no escapes recorded</span>'}
    </div>
    <div class="card"><h3>Invariant coverage</h3>
      <div class="row">${Object.entries(ib.by_status).map(([k,n])=>`<span class="tag">${esc(k)}: ${n}</span>`).join('')}</div>
      <div class="row" style="margin-top:6px">${Object.entries(ib.by_severity).map(([k,n])=>`<span class="tag">${esc(k)}: ${n}</span>`).join('')}</div>
      ${ib.candidate_red_ids.length?`<div class="row" style="margin-top:8px"><span class="tier-high">candidate-red (gap): ${ib.candidate_red_ids.map(esc).join(', ')}</span></div>`:''}
    </div>
    <div class="card"><h3>Honest gaps</h3>
      <div class="gap">MTTD — pending Phase 4 (needs introduced_at timestamp)</div>
      ${Object.entries(gaps).map(([k,why])=>`<div class="gap">${esc(k)} — ${esc(why)}</div>`).join('')}
    </div>`;
}

async function show(tab){
  current = tab;
  document.getElementById('tab-inbox').classList.toggle('active', tab==='inbox');
  document.getElementById('tab-health').classList.toggle('active', tab==='health');
  const st = document.getElementById('status');
  try { st.textContent='refreshing…'; await (tab==='inbox'?renderInbox():renderHealth()); st.textContent=''; }
  catch(e){ document.getElementById('app').innerHTML='<p class="muted">Error: '+esc(e.message)+'</p>'; st.textContent=''; }
}

show('inbox');
setInterval(() => show(current), 15000);  // live: poll every 15s
</script>
</body>
</html>
```

- [ ] **Step 3b: Wire the route and package the static file**

In `src/marshal_core/adapters/api.py`, add at the top with the other imports:

```python
from pathlib import Path
from fastapi.responses import FileResponse

_STATIC_DIR = Path(__file__).parent / "static"
```

Add this route at the end of the file:

```python
@app.get("/")
def spa():
    return FileResponse(_STATIC_DIR / "index.html")
```

Then ensure the static file is shipped with the package. In `pyproject.toml`, under the `[tool.setuptools.package-data]` section (create the section if absent), add:

```toml
[tool.setuptools.package-data]
"marshal_core" = ["adapters/static/*.html"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && python -m pytest tests/test_dashboard_api.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/adapters/api.py src/marshal_core/adapters/static/index.html pyproject.toml tests/test_dashboard_api.py
git commit -m "feat(dashboard): serve read-only SPA at GET /"
```

---

## Task 8: Full-suite regression check + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && python -m pytest -q`
Expected: all tests pass, including the pre-existing suite (confirms the new endpoints/imports did not regress `/webhook`, `/plan`, `/results` or `metrics()`).

- [ ] **Step 2: Manual live smoke against the real DB**

Run:
```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
MARSHAL_DB="sqlite:///$PWD/marshal.db" uvicorn marshal_core.adapters.api:app --port 8787 &
sleep 2
curl -s localhost:8787/api/health | python -m json.tool | head -30
curl -s localhost:8787/api/inbox | python -m json.tool | head -20
curl -s -o /dev/null -w "%{http_code} %{content_type}\n" localhost:8787/
kill %1
```
Expected: `/api/health` shows the real verdict distribution (pass 31 / needs_human 23 / block 1) and escape breakdown; `/api/inbox` lists the 23 `needs_human` runs; `/` returns `200 text/html`.

- [ ] **Step 3: Commit (docs only, if any notes added)**

No code change expected. If smoke revealed a fix, commit it with a descriptive message.

---

## Self-Review Notes (author)

- **Spec coverage:** §3.1 read-only API → Tasks 5–6; §5.1 Inbox (cards + evidence expand) → Task 7 `renderInbox` + Task 5 `/api/runs`; §5.2 Health four blocks → Task 7 `renderHealth` (MTTD block honestly deferred, matching §8.4 scope); §2 live-by-construction → Task 8 smoke against real DB; §7 testing (store aggregates + API contract) → Tasks 1–7 tests. Out-of-scope by design: `review_job`, re-review button, worker, `introduced_at_ts` (Phases 2–4, separate plans).
- **Placeholder scan:** none — every code step contains complete content.
- **Type consistency:** method names `list_needs_human` / `escape_breakdown` / `invariant_breakdown` / `verdict_timeseries` are used identically in Store definitions (Tasks 1–4) and endpoints (Tasks 5–6); endpoint JSON keys (`escape_breakdown`, `invariant_breakdown`, `verdict_timeseries`, `gate_runs_by_verdict`, `unavailable`) match what the SPA reads in Task 7.
```
