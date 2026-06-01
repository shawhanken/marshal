"""端到端: webhook → 派活 → 模拟 CI 回传 → 影子 Check Run。
本环境无 docker, 用 SQLite 临时文件库 (功能等价于 Postgres 跑全链路)。"""
import importlib
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "e2e.db"
    monkeypatch.setenv("MARSHAL_DB", f"sqlite:///{db_file}")
    import marshal_core.adapters.api as api
    importlib.reload(api)          # 让 app 用上面的 MARSHAL_DB 重新初始化
    return TestClient(api.app)


def test_full_slice_shadow(client):
    # 1) PR 事件
    webhook_payload = {
        "action": "synchronize",
        "repository": {"name": "node"},
        "pull_request": {"head": {"sha": "e2e123"}, "user": {"login": "alice"},
                         "labels": []},
        "_diff_paths": ["execution/src/execution/transaction.rs"],
    }
    r1 = client.post("/webhook", json=webhook_payload)
    assert r1.status_code == 200
    assert "econ.fee_conservation" in r1.json()["invariant_ids"]

    # 2) CI 回传全 pass
    result = {
        "job_id": "inv-e2e123", "schema_version": "1", "kind": "invariant",
        "payload": {"results": [
            {"invariant_id": i, "passed": True, "detail": ""}
            for i in r1.json()["invariant_ids"]]},
        "cost": 0.0, "status": "ok",
    }
    r2 = client.post("/results", json=result)
    assert r2.status_code == 200
    body = r2.json()
    assert body["verdict"] == "pass"
    assert body["check_run"]["conclusion"] == "neutral"   # 影子模式
    assert body["check_run"]["head_sha"] == "e2e123"
