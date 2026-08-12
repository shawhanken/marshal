import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from marshal_core.contracts import NormalizedEvent, StructuredResult
from marshal_core.executor import reporter
from marshal_core.knowledge.models import Base, ensure_schema
from marshal_core.knowledge.store import Store
from marshal_core.modules.invariant_gate import InvariantGate
from marshal_pack_cowboy.pack import CowboyPack


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _event():
    return NormalizedEvent(kind="pr", repo="node", change_ref="retro",
                           diff_paths=["execution/src/execution/transaction.rs"])


def test_gate_rejects_truthy_string_and_blocks_typed_failure():
    gate = InvariantGate(CowboyPack())
    event = _event()
    job = gate.build_dispatch(event)
    forged = StructuredResult(
        job_id=job.job_id, kind="invariant", status="ok",
        payload={"results": [{"invariant_id": item, "passed": "false"}
                              for item in job.params["invariant_ids"]]})
    assert gate.evaluate(event, job, forged).verdict == "escalate"

    failed = StructuredResult(
        job_id=job.job_id, kind="invariant", status="degraded",
        payload={"results": [{"invariant_id": job.params["invariant_ids"][0],
                              "passed": False}]})
    decision = gate.evaluate(event, job, failed)
    assert decision.verdict == "block"
    assert decision.gates[0]["outcome"] == "fail"


def _patch_reporter_brain(monkeypatch, plan, posted):
    def fake_urlopen(req, timeout=0):
        if req.full_url.endswith("/plan"):
            return _FakeResp(plan)
        posted["body"] = json.loads(req.data.decode())
        return _FakeResp({"verdict": "escalate"})
    monkeypatch.setattr(reporter.urllib.request, "urlopen", fake_urlopen)


def test_reporter_unknown_executor_is_degraded(monkeypatch):
    posted = {}
    _patch_reporter_brain(monkeypatch, {
        "job_id": "inv-retro",
        "invariants": [{"invariant_id": "a", "run_command": ["true"],
                        "location_repo": "node", "executor_kind": "typo"}],
    }, posted)
    executed = []
    monkeypatch.setattr(reporter.subprocess, "run",
                        lambda *args, **kwargs: executed.append(args) or _Proc())
    reporter.run("http://brain", "node", "retro", [])
    assert executed == []
    assert posted["body"]["status"] == "degraded"
    assert "unsupported" in posted["body"]["payload"]["not_run"][0]["reason"]


def test_reporter_malformed_plan_is_reported(monkeypatch):
    posted = {}
    _patch_reporter_brain(monkeypatch, {
        "job_id": "inv-retro",
        "invariants": [{"invariant_id": "a", "location_repo": "node"}],
    }, posted)
    reporter.run("http://brain", "node", "retro", [])
    assert posted["body"]["status"] == "degraded"
    assert posted["body"]["payload"]["results"] == []
    assert posted["body"]["payload"]["not_run"][0]["invariant_id"] == "a"


def test_reporter_total_timeout_is_bounded(monkeypatch):
    posted = {}
    _patch_reporter_brain(monkeypatch, {
        "job_id": "inv-retro",
        "invariants": [
            {"invariant_id": "a", "run_command": ["true"], "location_repo": "node",
             "executor_kind": "command"},
            {"invariant_id": "b", "run_command": ["true"], "location_repo": "node",
             "executor_kind": "command"},
        ],
    }, posted)
    monkeypatch.setattr(reporter, "_TOTAL_TIMEOUT_SEC", 0)
    executed = []
    monkeypatch.setattr(reporter.subprocess, "run",
                        lambda *args, **kwargs: executed.append(args) or _Proc())
    reporter.run("http://brain", "node", "retro", [])
    assert executed == []
    assert posted["body"]["status"] == "degraded"
    assert {item["invariant_id"] for item in posted["body"]["payload"]["not_run"]} == {"a", "b"}


def test_review_finding_rejects_orphan_and_upserts_existing():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    store = Store(session)
    with pytest.raises(ValueError, match="review run not found"):
        store.record_finding(run_id=9999, key="orphan")
    run = store.open_review_run(change_ref="retro")
    first = store.record_finding(run_id=run.id, key="k1", title="before")
    second = store.record_finding(run_id=run.id, key="k1", title="after")
    assert first.id == second.id
    assert len(store.list_findings(run.id)) == 1
    assert second.title == "after"
    session.close()


def test_existing_escape_schema_is_migrated(tmp_path):
    db = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE escape_registry DROP COLUMN fix_ref"))
        conn.execute(text("ALTER TABLE escape_registry DROP COLUMN missed_by_run_id"))
    ensure_schema(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("escape_registry")}
    assert {"fix_ref", "missed_by_run_id"} <= columns
    session = sessionmaker(bind=engine)()
    assert Store(session).list_escapes() == []
    session.close()


def test_results_reload_event_from_database(tmp_path, monkeypatch):
    monkeypatch.setenv("MARSHAL_DB", f"sqlite:///{tmp_path / 'api.db'}")
    import importlib
    import marshal_core.adapters.api as api
    api = importlib.reload(api)
    client = TestClient(api.app)
    planned = client.post("/plan", json={
        "kind": "pr", "repo": "node", "change_ref": "durable",
        "diff_paths": ["execution/src/execution/transaction.rs"],
    })
    assert planned.status_code == 200
    body = planned.json()
    api._EVENTS.clear()
    result = {
        "job_id": body["job_id"], "kind": "invariant", "status": "ok",
        "payload": {"results": [
            {"invariant_id": item["invariant_id"], "passed": True}
            for item in body["invariants"]
        ]},
    }
    response = client.post("/results", json=result)
    assert response.status_code == 200
    assert response.json()["verdict"] == "pass"


def test_human_verdict_cannot_be_overwritten():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    store = Store(session)
    run = store.open_review_run(change_ref="retro")
    finding = store.record_finding(run_id=run.id, key="k1")
    store.set_human_verdict(finding.id, "accepted")
    with pytest.raises(ValueError, match="already has"):
        store.set_human_verdict(finding.id, "rejected")
    session.close()


def test_ratchet_close_registers_check_atomically():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    store = Store(session)
    store.open_escape(id="esc", description="d", root_cause_class="rc")
    store.close_escape_with_invariant(
        "esc", spawned_check="inv.x", fix_ref="fix",
        invariant={"id": "inv.x", "domain_pack": "cowboy", "domain": "test",
                    "spec_ref": "CIP-1", "executor_kind": "command",
                    "location_repo": "node", "location_path": "x",
                    "location_test": "test_x", "severity": "mid"})
    assert store.get_escape("esc").status == "closed"
    assert store.list_invariants("cowboy", "node")[0].id == "inv.x"
    session.close()


def test_webhook_refuses_pr_beyond_files_api_limit(tmp_path, monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    import marshal_core.adapters.api as api

    monkeypatch.setenv("MARSHAL_DB", f"sqlite:///{tmp_path / 'limit.db'}")
    api = importlib.reload(api)

    payload = {
        "repository": {"name": "node", "full_name": "cowboyinc/node"},
        "pull_request": {
            "number": 7, "head": {"sha": "limit"},
            "user": {"login": "alice"}, "labels": [], "changed_files": 3001,
        },
    }

    class _Never:
        def __init__(self, **kwargs):
            raise AssertionError("must reject before calling GitHub")

    monkeypatch.setattr(api.httpx, "AsyncClient", _Never)
    response = TestClient(api.app).post("/webhook", json=payload)
    assert response.status_code == 502
    assert "3000-file API limit" in response.json()["detail"]


def test_webhook_refuses_incomplete_files_api_page(tmp_path, monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    import marshal_core.adapters.api as api

    monkeypatch.setenv("MARSHAL_DB", f"sqlite:///{tmp_path / 'incomplete.db'}")
    api = importlib.reload(api)

    payload = {
        "repository": {"name": "node", "full_name": "cowboyinc/node"},
        "pull_request": {
            "number": 7, "head": {"sha": "incomplete"},
            "user": {"login": "alice"}, "labels": [], "changed_files": 2,
        },
    }

    class _OnePage:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return type("Response", (), {
                "raise_for_status": lambda self: None,
                "json": lambda self: [{"filename": "only-one.rs"}],
            })()

    monkeypatch.setattr(api.httpx, "AsyncClient", lambda **kwargs: _OnePage())
    response = TestClient(api.app).post("/webhook", json=payload)
    assert response.status_code == 502
    assert "expected 2" in response.json()["detail"]
