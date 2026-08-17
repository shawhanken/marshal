import importlib
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "jobs.db"
    monkeypatch.setenv("MARSHAL_DB", f"sqlite:///{db_file}")
    monkeypatch.delenv("MARSHAL_JOB_TOKEN", raising=False)  # dev mode: no token
    import marshal_core.adapters.api as api
    importlib.reload(api)
    return TestClient(api.app)


def test_create_job_returns_pending(client):
    r = client.post("/api/jobs", json={"change_ref": "node#7", "repo": "node"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["kind"] == "mechanical"
    assert body["change_ref"] == "node#7"


def test_get_job_roundtrip_and_404(client):
    job_id = client.post("/api/jobs", json={"change_ref": "node#7"}).json()["id"]
    r = client.get(f"/api/jobs/{job_id}")
    assert r.status_code == 200
    assert r.json()["change_ref"] == "node#7"
    assert client.get("/api/jobs/99999").status_code == 404


def test_create_job_requires_change_ref(client):
    assert client.post("/api/jobs", json={"repo": "node"}).status_code == 422


def test_create_job_rejects_bad_kind(client):
    r = client.post("/api/jobs", json={"change_ref": "x", "kind": "bogus"})
    assert r.status_code == 422


def test_token_guard_blocks_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("MARSHAL_DB", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("MARSHAL_JOB_TOKEN", "secret")
    import marshal_core.adapters.api as api
    importlib.reload(api)
    c = TestClient(api.app)
    assert c.post("/api/jobs", json={"change_ref": "x"}).status_code == 403
    ok = c.post("/api/jobs", json={"change_ref": "x"},
                headers={"X-Marshal-Token": "secret"})
    assert ok.status_code == 200


def test_get_job_token_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("MARSHAL_DB", f"sqlite:///{tmp_path}/g.db")
    monkeypatch.setenv("MARSHAL_JOB_TOKEN", "secret")
    import marshal_core.adapters.api as api
    importlib.reload(api)
    c = TestClient(api.app)
    created = c.post("/api/jobs", json={"change_ref": "x"},
                     headers={"X-Marshal-Token": "secret"})
    assert created.status_code == 200
    jid = created.json()["id"]
    # GET without token -> 403
    assert c.get(f"/api/jobs/{jid}").status_code == 403
    # GET with token -> 200
    assert c.get(f"/api/jobs/{jid}", headers={"X-Marshal-Token": "secret"}).status_code == 200
