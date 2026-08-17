# Marshal Dashboard — Phase 4 (introduced_at_ts + MTTD) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `introduced_at_ts` timestamp column to `escape_registry` and compute a real **mean-time-to-detection (MTTD)** metric, surfaced on the Health screen — replacing the honest "pending Phase 4" placeholder. Escapes without the timestamp are excluded from the mean and counted honestly.

**Architecture:** Additive column + an idempotent `ensure_schema()` migration (so the pre-existing `marshal.db`, whose `escape_registry` lacks the column, gets it via `ALTER TABLE ADD COLUMN` on next startup rather than crashing on reads). MTTD = `discovered_at − introduced_at_ts` averaged over escapes that have both; `metrics()` returns it as a real value and drops it from the `unavailable` list; the SPA renders it.

**Tech Stack:** SQLAlchemy 2.0 (`inspect`, `text` for the migration), pytest. Frontend is dependency-free vanilla JS.

**Scope note:** Phase 4 of the design spec (`docs/superpowers/specs/2026-08-13-marshal-dashboard-design.md`, §8.4 / §4.2). Per the settled design: **only `introduced_at_ts` is added** (not the escape_rate denominator or a Classifications table — those stay in `unavailable`). No backfill of the 22 existing escapes; they have `introduced_at_ts = NULL` and are honestly counted as "excluded" until new escapes are recorded with the timestamp.

**Faithful note:** `escape_registry` already has a free-string `introduced_at` column; this adds a **separate** typed `introduced_at_ts` column (does not retype the old one), so existing data and the existing `metrics()` `unavailable` reason for it are untouched until this phase flips it.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/marshal_core/knowledge/models.py` | `introduced_at_ts` column on `EscapeRegistry`; `ensure_schema(engine)` (create_all + idempotent ALTER) | Modify |
| `src/marshal_core/adapters/api.py` | call `ensure_schema` instead of bare `create_all` | Modify |
| `src/marshal_core/worker.py` | call `ensure_schema` instead of bare `create_all` | Modify |
| `src/marshal_core/cli.py` | call `ensure_schema` instead of bare `create_all` | Modify |
| `src/marshal_core/knowledge/store.py` | `mttd()` computation; wire real value into `metrics()` | Modify |
| `src/marshal_core/adapters/static/index.html` | render real MTTD, drop "pending Phase 4" | Modify |
| `tests/test_schema_migration.py` | `ensure_schema` adds the column to a pre-existing table | Create |
| `tests/test_mttd.py` | `Store.mttd()` computation | Create |
| `tests/test_metrics.py` | update the stale `unavailable` assertion | Modify |
| `tests/test_dashboard_api.py` | update the stale `unavailable` assertion + assert real MTTD in payload | Modify |

---

## Task 1: `introduced_at_ts` column + `ensure_schema` migration

**Files:**
- Modify: `src/marshal_core/knowledge/models.py`, `src/marshal_core/adapters/api.py`, `src/marshal_core/worker.py`, `src/marshal_core/cli.py`
- Test: `tests/test_schema_migration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_schema_migration.py`:

```python
from sqlalchemy import create_engine, inspect, text
from marshal_core.knowledge.models import ensure_schema


def test_ensure_schema_adds_missing_introduced_at_ts(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/old.db")
    # simulate a pre-existing DB whose escape_registry predates the new column
    with eng.begin() as c:
        c.execute(text("CREATE TABLE escape_registry "
                       "(id VARCHAR PRIMARY KEY, status VARCHAR)"))
    ensure_schema(eng)
    cols = {col["name"] for col in inspect(eng).get_columns("escape_registry")}
    assert "introduced_at_ts" in cols


def test_ensure_schema_fresh_db_has_column(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/fresh.db")
    ensure_schema(eng)
    cols = {col["name"] for col in inspect(eng).get_columns("escape_registry")}
    assert "introduced_at_ts" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_schema_migration.py -v`
Expected: FAIL with `ImportError: cannot import name 'ensure_schema'`

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/knowledge/models.py`:

(a) extend the sqlalchemy import at the top. Change:
```python
from sqlalchemy import String, Integer, JSON, DateTime
```
to:
```python
from sqlalchemy import String, Integer, JSON, DateTime, inspect, text
```

(b) add the `introduced_at_ts` column to `EscapeRegistry`, right after the existing `introduced_at` line:
```python
    introduced_at_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

(c) add `ensure_schema` at the END of the file:
```python
def ensure_schema(engine) -> None:
    """create_all + idempotent additive column migrations, so a DB created before a
    column was added to a model gets it on next startup instead of erroring on reads.
    Safe to call at every startup. The ALTER only ever fires against an older SQLite
    marshal.db (a fresh create_all — SQLite or Postgres — already includes the column,
    so the guard skips it)."""
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("escape_registry")}
    if "introduced_at_ts" not in cols:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE escape_registry ADD COLUMN introduced_at_ts DATETIME"))
```

Then wire it at the three engine-init sites (replace `Base.metadata.create_all(<engine>)` with `ensure_schema(<engine>)`):

- `src/marshal_core/adapters/api.py:20` — change `Base.metadata.create_all(_engine)` to `ensure_schema(_engine)`; update its import line `from marshal_core.knowledge.models import Base` to `from marshal_core.knowledge.models import Base, ensure_schema`.
- `src/marshal_core/worker.py:167` — change `Base.metadata.create_all(engine)` to `ensure_schema(engine)`; update `from marshal_core.knowledge.models import Base` to `from marshal_core.knowledge.models import Base, ensure_schema`.
- `src/marshal_core/cli.py:38` — change `Base.metadata.create_all(engine)` to `ensure_schema(engine)`; update the models import to include `ensure_schema` (find the `from marshal_core.knowledge.models import ...` line and add `ensure_schema`; if `Base` is imported there, add alongside it).

(Leave `cli.py:205`'s snapshot `create_all` as-is — it builds a throwaway source engine from a snapshot file and does not need the migration.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_schema_migration.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/knowledge/models.py src/marshal_core/adapters/api.py src/marshal_core/worker.py src/marshal_core/cli.py tests/test_schema_migration.py
git commit -m "feat(dashboard): introduced_at_ts column + idempotent ensure_schema migration"
```

---

## Task 2: `Store.mttd()` computation

**Files:**
- Modify: `src/marshal_core/knowledge/store.py`
- Test: `tests/test_mttd.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mttd.py`:

```python
from datetime import datetime, timezone, timedelta
from marshal_core.knowledge.store import Store


def test_mttd_computes_over_timestamped_escapes(db_session):
    s = Store(db_session)
    t0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    s.open_escape(id="e1", description="d", root_cause_class="c",
                  introduced_at_ts=t0, discovered_at=t0 + timedelta(days=4))
    s.open_escape(id="e2", description="d", root_cause_class="c",
                  introduced_at_ts=t0, discovered_at=t0 + timedelta(days=6))
    s.open_escape(id="e3", description="d", root_cause_class="c")  # no ts -> excluded
    m = s.mttd()
    assert m["count"] == 2
    assert m["mean_days"] == 5.0
    assert m["excluded"] == 1


def test_mttd_no_timestamped_escapes_is_honest(db_session):
    s = Store(db_session)
    s.open_escape(id="e1", description="d", root_cause_class="c")
    m = s.mttd()
    assert m["mean_days"] is None
    assert m["count"] == 0
    assert m["excluded"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_mttd.py -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'mttd'`

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/knowledge/store.py`, add this method to the `Store` class (place it right after `metrics`):

```python
    def mttd(self) -> dict:
        # Mean time-to-detection over escapes that have both an introduced_at_ts and a
        # discovered_at. Escapes lacking the timestamp are excluded and counted honestly
        # (no fabricated interval), matching the metrics() honesty policy.
        rows = self.s.execute(
            select(EscapeRegistry.discovered_at, EscapeRegistry.introduced_at_ts)).all()
        deltas, excluded = [], 0
        for discovered_at, introduced_at_ts in rows:
            if discovered_at is not None and introduced_at_ts is not None:
                deltas.append((discovered_at - introduced_at_ts).total_seconds())
            else:
                excluded += 1
        if not deltas:
            return {"mean_days": None, "count": 0, "excluded": excluded}
        return {"mean_days": round(sum(deltas) / len(deltas) / 86400, 2),
                "count": len(deltas), "excluded": excluded}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_mttd.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/knowledge/store.py tests/test_mttd.py
git commit -m "feat(dashboard): Store.mttd mean-time-to-detection over timestamped escapes"
```

---

## Task 3: Wire real MTTD into `metrics()` (drop from `unavailable`)

**Files:**
- Modify: `src/marshal_core/knowledge/store.py`
- Modify: `tests/test_metrics.py`
- Modify: `tests/test_dashboard_api.py`

- [ ] **Step 1: Update the stale tests (they assert the OLD behavior) + add the new assertion**

In `tests/test_metrics.py`, find `test_metrics_marks_unavailable_honestly` (around line 31-35):
```python
def test_metrics_marks_unavailable_honestly(db_session):
    s = Store(db_session)
    m = s.metrics()
    for k in ("escape_rate", "mean_time_to_detection", "tiered_review_coverage"):
        assert k in m["unavailable"]
```
Replace it with:
```python
def test_metrics_marks_unavailable_honestly(db_session):
    s = Store(db_session)
    m = s.metrics()
    # escape_rate + tiered coverage remain honestly unavailable
    for k in ("escape_rate", "tiered_review_coverage"):
        assert k in m["unavailable"]
    # MTTD is now a real computed value, no longer in `unavailable`
    assert "mean_time_to_detection" not in m["unavailable"]
    assert m["mean_time_to_detection"]["mean_days"] is None      # no timestamped escapes
    assert m["mean_time_to_detection"]["count"] == 0
```

In `tests/test_dashboard_api.py`, find (around line 54):
```python
    assert "mean_time_to_detection" in body["unavailable"]
```
Replace it with:
```python
    assert "mean_time_to_detection" not in body["unavailable"]
    assert "mean_time_to_detection" in body                      # now a real top-level metric
    assert "count" in body["mean_time_to_detection"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_metrics.py::test_metrics_marks_unavailable_honestly tests/test_dashboard_api.py::test_health_composes_metrics_and_breakdowns -v`
Expected: FAIL — `metrics()` still lists `mean_time_to_detection` in `unavailable` and has no top-level key.

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/knowledge/store.py`, in the `metrics()` return dict:

(a) add a real MTTD key. After the `"gate_runs_by_verdict": gate_by_verdict,` line, add:
```python
            "mean_time_to_detection": self.mttd(),
```

(b) remove the now-obsolete `unavailable` entry. Delete this exact line from the `unavailable` dict:
```python
                "mean_time_to_detection": "needs introduced_at as a timestamp (currently free string)",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_metrics.py tests/test_dashboard_api.py -v`
Expected: PASS (all — the updated assertions plus every other metrics/dashboard test)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/knowledge/store.py tests/test_metrics.py tests/test_dashboard_api.py
git commit -m "feat(dashboard): metrics() surfaces real MTTD, drops it from unavailable"
```

---

## Task 4: SPA renders real MTTD

**Files:**
- Modify: `src/marshal_core/adapters/static/index.html`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_api.py`:

```python
def test_spa_renders_real_mttd_not_placeholder(client):
    html = client.get("/").text
    assert "mean_time_to_detection" in html      # SPA reads the real metric
    assert "pending Phase 4" not in html          # placeholder is gone
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_dashboard_api.py::test_spa_renders_real_mttd_not_placeholder -v`
Expected: FAIL (the SPA still has the "pending Phase 4" placeholder and doesn't read `mean_time_to_detection`)

- [ ] **Step 3: Write minimal implementation**

In `src/marshal_core/adapters/static/index.html`, in `renderHealth()`:

(a) find this existing line (near the top of `renderHealth`, with the other `const` declarations):
```
  const gaps = h.unavailable || {};
```
and add immediately after it:
```
  const mttd = h.mean_time_to_detection || {};
```

(b) find this exact line (inside the "Honest gaps" card template):
```
      <div class="gap">MTTD — pending Phase 4 (needs introduced_at timestamp)</div>
```
and replace it with:
```
      <div class="gap">MTTD — ${mttd.mean_days!=null ? esc(mttd.mean_days)+' days (mean over '+esc(mttd.count)+' escapes)' : 'no timestamped escapes yet'}${mttd.excluded ? ' · '+esc(mttd.excluded)+' excluded (no introduced_at timestamp)' : ''}</div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest tests/test_dashboard_api.py -v`
Expected: PASS (all dashboard_api tests incl. the new one)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
git add src/marshal_core/adapters/static/index.html tests/test_dashboard_api.py
git commit -m "feat(dashboard): Health renders real MTTD, drops the pending-Phase-4 placeholder"
```

---

## Task 5: Full-suite regression + live migration smoke

**Files:** none (verification only)

- [ ] **Step 1: Full-suite regression**

Run: `cd /home/ubuntu/.claude/plugins/marketplaces/marshal && venv/bin/python -m pytest -q`
Expected: all new/changed tests pass; the ONLY failures are the pre-existing `marshal_core/checks/test_system_actor_addrmap.py` pair (prove with `git log <phase4-base>..HEAD -- <file>` empty). No Phase 4 test (`test_schema_migration`, `test_mttd`, `test_metrics`, `test_dashboard_api`) fails.

- [ ] **Step 2: Live migration + MTTD smoke against a COPY of the real DB**

The real `marshal.db` predates the column. Verify `ensure_schema` migrates it cleanly and MTTD reads without crashing — against a COPY, so the real DB is untouched by the smoke (the live app migrates the real DB automatically on next startup):
```bash
cd /home/ubuntu/.claude/plugins/marketplaces/marshal
cp marshal.db /tmp/mttd_smoke.db
MARSHAL_DB="sqlite:////tmp/mttd_smoke.db" venv/bin/python -c "
import os
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from marshal_core.knowledge.models import ensure_schema
from marshal_core.knowledge.store import Store
eng = create_engine(os.environ['MARSHAL_DB'])
ensure_schema(eng)                       # migrates the old escape_registry
assert 'introduced_at_ts' in {c['name'] for c in inspect(eng).get_columns('escape_registry')}
with sessionmaker(bind=eng)() as s:
    st = Store(s)
    m = st.metrics()
    print('mttd:', m['mean_time_to_detection'])
    print('mean_time_to_detection in unavailable:', 'mean_time_to_detection' in m['unavailable'])
    # 22 pre-existing escapes have no introduced_at_ts -> all excluded, mean None, no crash
    assert m['mean_time_to_detection']['mean_days'] is None
    assert m['mean_time_to_detection']['excluded'] == 22
    assert 'mean_time_to_detection' not in m['unavailable']
    print('escape_breakdown still reads:', len(st.escape_breakdown()), 'root-cause groups')
"
rm -f /tmp/mttd_smoke.db
echo "MTTD migration smoke OK"
```
Expected: prints `mttd: {'mean_days': None, 'count': 0, 'excluded': 22}`, `mean_time_to_detection in unavailable: False`, the escape breakdown still reads (no crash from the new column), and `MTTD migration smoke OK`.

- [ ] **Step 3: No commit** (verification only). If a fix was needed, commit it descriptively.

---

## Self-Review Notes (author)

- **Spec coverage:** §4.2 additive `introduced_at_ts` (new column, not a retype) → Task 1; the migration necessity (existing marshal.db lacks the column) → `ensure_schema` at all engine sites (Task 1); §5.2 MTTD block honestly excluding un-timestamped escapes → Tasks 2-4; §8.4 "additive column + the one trend metric it unlocks" → whole plan; honesty policy (escape_rate + tiered coverage still `unavailable`, MTTD now real) → Task 3. No backfill of existing escapes (out of scope, honestly excluded).
- **Placeholder scan:** none — every code step is complete.
- **Type consistency:** `mttd()` returns `{mean_days, count, excluded}` used identically in `metrics()` (Task 3), the tests (Tasks 2-3), and the SPA's `h.mean_time_to_detection.{mean_days,count,excluded}` (Task 4). `ensure_schema(engine)` signature matches its call sites at api.py/worker.py/cli.py (Task 1). `introduced_at_ts` column name is identical across model, migration ALTER, `mttd()` query, and `open_escape(introduced_at_ts=...)` in tests.
```
