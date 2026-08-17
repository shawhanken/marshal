# Marshal Dashboard — Design Spec

**Date:** 2026-08-13
**Status:** Approved (brainstorm), pending implementation plan
**Scope:** An operational + analytics control plane for Marshal, served by the existing
`marshal_core` FastAPI service.

---

## 1. Goal & Positioning

Marshal already persists everything it does in a structured SQLite database
(`marshal.db`) via `marshal_core.knowledge.store.Store`. The dashboard is **not a new
project** — it is a visualization + control layer mounted on the *existing* FastAPI app.
It answers two questions at once:

- **Inbox (operational):** "What needs my attention right now?" — the `needs_human`
  review queue, made actionable.
- **Health (analytics):** "Is this gate getting better?" — verdict distribution, escape
  root-cause breakdown, invariant coverage, and detection latency over time.

It is a **control plane**, not just a viewer: a one-click **re-review** button can
re-trigger both the mechanical gate *and* a full deep adversarial review (headless
Claude) via a background job system.

### Non-goals (deliberately deferred)
- No `escape_rate` metric — there is no honest total-bug denominator, and Marshal does
  not fabricate one. Surfaced as an explicit "insufficient data" note.
- No tiered-review-coverage metric — needs a `Classifications` table not modeled yet.
- No public/remote exposure — the control endpoints are `localhost`-only.

---

## 2. Data Source (existing, unchanged)

Four existing tables in `marshal.db`:

| Table | Content |
|---|---|
| `gate_run` | one row per gate execution: `verdict` (`pass` / `needs_human` / `block`), `evidence` JSON (PR#, repo, tier, CIP, dimensions, invariants run/pass, findings, advisory, `verified` claims), `created_at` |
| `escape_registry` | escaped bugs (the ratchet): `root_cause_class`, `status`, `spawned_check`, `introduced_at` (free string) |
| `invariant_registry` | registered invariant gates: `severity`, `status` (`active` / `candidate-red`), `spec_ref`, location |
| `audit_log` | decision event stream |

The dashboard **reads the same DB the orchestrator writes to**, so "live" is intrinsic —
no cache, no sync.

---

## 3. Architecture

Three units, each with a single responsibility, decoupled through DB tables as
interfaces. The web layer and worker **never talk directly** — they coordinate only
through the `review_job` state machine and the existing `gate_run` table. Either can be
restarted or tested independently.

```
┌─────────────────────────────────────────────────────────┐
│  marshal_core FastAPI app  (same uvicorn process)         │
│                                                           │
│  EXISTING (untouched):  POST /webhook  /plan  /results    │
│                                                           │
│  NEW — read-only API:                                     │
│    GET /api/inbox      → needs_human queue                │
│    GET /api/health     → aggregate metrics                │
│    GET /api/runs/{id}  → single gate_run evidence         │
│    GET /api/escapes    → ratchet list                     │
│                                                           │
│  NEW — job control (localhost + token only):              │
│    POST /api/jobs      → enqueue a re-review job          │
│    GET  /api/jobs/{id} → poll status                      │
│                                                           │
│  NEW — static frontend:                                   │
│    GET /  → single-page app (vanilla JS + inline charts)  │
└──────────────┬────────────────────────────┬───────────────┘
               │ read/write same SQLite       │ write review_job
               ▼                              ▼
        marshal.db (source of truth)     review_job (new table)
                                              ▲
                                              │ atomic claim / write-back
                                    ┌─────────┴──────────┐
                                    │  worker (separate   │
                                    │  process):          │
                                    │  poll review_job →  │
                                    │  mechanical: Orch.  │
                                    │  deep: headless     │
                                    │  Claude in isolated │
                                    │  worktree           │
                                    └─────────────────────┘
```

### 3.1 Web / API layer (FastAPI)
Only reads the DB and accepts job requests (writing `review_job`). **Never runs a review
itself.** New GET endpoints are pure projections of existing store methods
(`metrics()`, `list_open_escapes()`, `get_gate_run()`, plus a couple of new aggregates).

### 3.2 Worker (separate process: `python -m marshal_core.worker`)
Claims `pending` jobs, runs them, writes results back. Survives independently of the web
process.

### 3.3 Frontend SPA
Vanilla JS, polls the GET endpoints, stateless. Self-contained (no CDN); one lightweight
inline-able chart approach. Styling follows the `dataviz` skill (light/dark, accessible,
one visual system).

---

## 4. Data Model Changes

Additive only. **No change to the semantics of the four existing tables.**

### 4.1 New table `review_job` (job state machine)
```
id            INTEGER PK
change_ref    VARCHAR      -- PR/commit under re-review
kind          VARCHAR      -- 'mechanical' | 'deep'
status        VARCHAR      -- pending -> running -> done | failed
requested_by  VARCHAR      -- who pressed the button (audit)
created_at    DATETIME
started_at    DATETIME     NULL
finished_at   DATETIME     NULL
result_run_id INTEGER      NULL  -- FK to the gate_run produced on success
error         VARCHAR      NULL  -- reason when failed
```
Atomic claim (prevents two workers grabbing the same job):
```sql
UPDATE review_job SET status='running', started_at=:now
WHERE id = (SELECT id FROM review_job WHERE status='pending'
            ORDER BY created_at LIMIT 1)
  AND status='pending';
```

### 4.2 New column on `escape_registry`
```
introduced_at_ts  DATETIME  NULL   -- new column; does NOT replace the free-string introduced_at
```
- A **new column**, not a retype of `introduced_at`: old rows need no migration and the
  existing `metrics()` logic is unaffected.
- MTTD is computed only for escapes that have `introduced_at_ts`; rows without it are
  **excluded from the denominator and labeled "insufficient data"**.

### 4.3 Deliberately NOT added
- `Classifications` table (tiered coverage) — deferred until real need.
- Total-bug denominator for `escape_rate` — cannot be measured honestly; not faked.

---

## 5. Screens

Single page, top toggle between **Inbox** and **Health**. Both poll the GET endpoints.

### 5.1 Inbox — "what needs my attention"
`GET /api/inbox` returns `gate_run` rows with `verdict='needs_human'`, newest first.
Each card flattens fields already present in `evidence`:

```
┌────────────────────────────────────────────────────────┐
│ ⚠ node#1302  ·  CIP-13  ·  tier: high  ·  2h ago         │
│ dimensions: correctness spec cross-repo security econ     │
│ invariants: 10/10 pass   ·   high-sev findings: 0         │
│ ▸ 2 advisory (ratchet candidate)          [evidence][re-review]│
└────────────────────────────────────────────────────────┘
```
- **Filter/sort:** tier (high pinned), repo, CIP, time. Header counters:
  `needs_human N · block N`.
- **[evidence]** → expands the full evidence for that run (invariant list, `verified`
  claims, `advisory_findings`, `extra_tests` counts). This is where the audit/traceability
  concern is absorbed — an expansion layer on the card, not a separate screen.
- **[re-review]** → `POST /api/jobs {change_ref, kind}`; the card grows an inline job
  status row (pending → running → done) and swaps in the new verdict on completion.

### 5.2 Health — "is the gate getting better"
`GET /api/health` consumes the existing `Store.metrics()` plus new aggregates. Four blocks,
each charting **only what is computable**:

1. **Gate output:** pass / needs_human / block distribution + stacked area over time.
2. **Ratchet (escapes):** the 22 escapes grouped by `root_cause_class` (state-consensus 5,
   econ-conservation 5, cross-repo 5, …) — shows at a glance which bug class keeps
   escaping. Each bar expands to its `spawned_check`.
3. **Invariant coverage:** the 24 invariants by `active` / `candidate-red` / severity;
   `candidate-red` highlighted (the "should have a gate but not green yet" gap).
4. **MTTD (newly unlocked):** distribution of introduced→detected latency, computed only
   for escapes with `introduced_at_ts`. Escapes lacking it are labeled "N rows: insufficient
   data, excluded."

**Honest-gap footer** (continues `metrics().unavailable`):
> `escape_rate` — no total-bug denominator, not estimated.
> `tiered_coverage` — needs a Classifications table.

---

## 6. Headless-Claude Worker (highest-risk unit)

Turns the board into a control plane. Designed around four rules: **isolation, rate-limit,
timeout, honest reporting.**

### 6.1 Main loop (`python -m marshal_core.worker`)
```
loop:
  job = atomically claim one pending row (see §4.1)
  if job.kind == 'mechanical':
      call Orchestrator.plan()            # pure Python, fast, no side effects
  if job.kind == 'deep':
      run headless Claude on the marshal skill inside an ISOLATED worktree
  on success: write a new gate_run verdict + set review_job.status='done',
              result_run_id=<new gate_run id>
  on failure: status='failed', error=<reason>   # never swallow, never leave 'running'
```

### 6.2 Deep-review invocation
- `claude -p "<marshal skill on {change_ref}>" --output-format json`; the worker parses
  the JSON for verdict + findings.
- **Isolation:** each deep job runs in its **own git worktree**, never sharing with
  concurrent agents or the main tree. Worktree lives on a **stable path (NOT `/tmp`)** —
  `/tmp` worktrees get reaped mid-run — and is torn down when done.
- **Rate-limit:** the worker runs **at most one deep job at a time** (deep review fans out
  many sub-agents; concurrent deep jobs would overload the machine). Mechanical jobs may
  run at small concurrency. Excess jobs queue and honestly display `pending`.
- **Timeout:** hard wall-clock cap per deep job (e.g. 30 min); on timeout the job is
  killed → `failed` + error, worktree cleaned. **A job never runs unbounded and blocks the
  queue.**

### 6.3 Auth / boundary
`POST /api/jobs` is bound to `localhost` + a local token (env var) because it can spend
real Claude compute. It must not be exposed on the network.

### 6.4 Honesty / provenance
A verdict written back by a deep job records `source: 'dashboard-worker'` and the `job_id`
in its `evidence`, so it is **distinguishable and auditable** from a verdict you produced
by running the skill by hand. Automated re-reviews never silently pollute the
hand-reviewed trail.

### 6.5 Failure modes — all have an exit
- Worker crashes → job stuck in `running` but has `started_at`; web shows "timed out, can
  requeue".
- Claude returns invalid JSON → `failed` + raw output stored in `error`.
- Worktree creation fails → job is not claimed, stays `pending`.

---

## 7. Testing Strategy

- **Store aggregates:** unit-test the new read queries (`/api/inbox`, `/api/health`
  aggregates, MTTD with/without `introduced_at_ts`) against a seeded in-memory DB.
- **Job state machine:** test the atomic claim under simulated concurrency (two claimers,
  one job → exactly one wins); test every transition incl. failure/timeout paths.
- **Worker (mechanical):** end-to-end against a real seeded DB, asserting a `gate_run` +
  `done` job appear.
- **Worker (deep):** the Claude invocation is behind an injectable boundary so tests stub
  it; assert isolation (worktree path is not `/tmp`), timeout kill, and `failed`-on-bad-JSON.
- **API contract:** FastAPI `TestClient` over each GET/POST, incl. localhost/token guard
  on `POST /api/jobs`.

---

## 8. Delivery Phasing

1. **Read-only board first** — the GET endpoints + SPA (Inbox + Health). Immediately more
   useful than today, zero new infrastructure beyond the FastAPI routes.
2. **Job abstraction + mechanical re-review** — `review_job` table, `POST/GET /api/jobs`,
   worker doing `mechanical` kind only. UI designed for job status from day one.
3. **Deep re-review** — worker `deep` kind: headless Claude in isolated worktree, rate
   limit, timeout, provenance. No frontend or API-contract rewrite (the job abstraction
   already exists).
4. **`introduced_at_ts` + MTTD** — additive column and the one trend metric it unlocks.
```
