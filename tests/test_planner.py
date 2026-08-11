"""测试 Orchestrator.plan() 返回 run-specs。"""
from marshal_core.contracts import NormalizedEvent
from marshal_core.knowledge.store import Store
from marshal_core.modules.orchestrator import Orchestrator
from marshal_pack_cowboy.pack import CowboyPack


def test_plan_returns_job_and_run_specs(db_session):
    orch = Orchestrator(pack=CowboyPack(), store=Store(db_session))
    ev = NormalizedEvent(kind="pr", repo="node", change_ref="abc123",
                         diff_paths=["execution/src/execution/transaction.rs"])
    plan = orch.plan(ev)
    assert plan.job_id == "inv-abc123"
    ids = {i["invariant_id"] for i in plan.invariants}
    assert "econ.fee_conservation" in ids
    fee = next(i for i in plan.invariants if i["invariant_id"] == "econ.fee_conservation")
    assert fee["run_command"][:2] == ["cargo", "test"]


def test_plan_declares_where_each_invariant_lives(db_session):
    orch = Orchestrator(pack=CowboyPack(), store=Store(db_session))
    ev = NormalizedEvent(kind="pr", repo="wallet", change_ref="w1",
                         diff_paths=["src/lib/cbor/encode.js"])
    plan = orch.plan(ev)
    tx = next(i for i in plan.invariants
              if i["invariant_id"] == "contract.tx_encoding_roundtrip")
    assert tx["location_repo"] == "node"      # lives in node, not in wallet
    assert tx["executor_kind"]


def test_plan_empty_for_unknown_repo(db_session):
    orch = Orchestrator(pack=CowboyPack(), store=Store(db_session))
    ev = NormalizedEvent(kind="pr", repo="other", change_ref="z1")
    assert orch.plan(ev).invariants == []


def test_plan_endpoint(tmp_path, monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    monkeypatch.setenv("MARSHAL_DB", f"sqlite:///{tmp_path/'p.db'}")
    import marshal_core.adapters.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    r = client.post("/plan", json={"kind": "pr", "repo": "node",
                                   "change_ref": "abc123",
                                   "diff_paths": ["execution/src/x.rs"]})
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == "inv-abc123"
    assert any(i["invariant_id"] == "econ.fee_conservation" for i in body["invariants"])
