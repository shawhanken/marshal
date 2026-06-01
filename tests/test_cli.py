import json
import subprocess
import sys
import os


def _run(args, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    proc = subprocess.run([sys.executable, "-m", "marshal_core.cli", *args],
                          capture_output=True, text=True, env=e,
                          cwd=os.path.dirname(os.path.dirname(__file__)))
    return proc


def test_classify_returns_json_tier():
    proc = _run(["classify", "--repo", "node",
                 "--paths", "execution/src/execution/engine.rs"])
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["tier"] == "high"
    assert "review_dimensions" in out


def test_classify_docs_only_low():
    proc = _run(["classify", "--repo", "node", "--paths", "README.md"])
    out = json.loads(proc.stdout)
    assert out["tier"] == "low"


def test_invariants_lists_run_commands():
    proc = _run(["invariants", "--repo", "node",
                 "--paths", "execution/src/execution/transaction.rs"])
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    ids = [i["id"] for i in out]
    assert "econ.fee_conservation" in ids
    assert all("run_command" in i for i in out)


def test_invariants_cross_repo_contract():
    proc = _run(["invariants", "--repo", "wallet", "--paths", "src/lib/cbor.js"])
    out = json.loads(proc.stdout)
    assert "contract.tx_encoding_roundtrip" in [i["id"] for i in out]


def test_spec_source_resolves_cip():
    proc = _run(["spec-source", "--ref", "CIP-3"])
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["source"]["path_glob"] == "docs/cips/cip-3-*.md"
    assert out["source"]["repo"] == "cowboy"


def test_spec_source_unknown_ref_is_null():
    proc = _run(["spec-source", "--ref", "C-1"])
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["source"] is None


def test_ratchet_open_then_close(tmp_path):
    db = {"MARSHAL_DB": f"sqlite:///{tmp_path/'t.db'}"}
    op = _run(["ratchet-open", "--desc", "bare 2**10000 逃逸",
               "--root-cause", "determinism-gap", "--escape-id", "esc-t1"], env=db)
    assert op.returncode == 0, op.stderr
    assert json.loads(op.stdout)["escape_id"] == "esc-t1"

    inv = json.dumps({
        "id": "det.bare_pow_literal", "domain": "determinism", "spec_ref": "M-B",
        "executor_kind": "proptest", "location_repo": "node",
        "location_path": "execution/src/pvm_executor.rs",
        "location_test": "prop_bare_pow_literal_blocked", "severity": "high"})
    cl = _run(["ratchet-close", "--escape-id", "esc-t1",
               "--spawned-check", "det.bare_pow_literal", "--inv-json", inv], env=db)
    assert cl.returncode == 0, cl.stderr
    assert json.loads(cl.stdout)["ok"] is True


def test_ratchet_close_tolerates_run_command_in_inv_json(tmp_path):
    # 文档化的棘轮流程让 skill 起草含 run_command 的 InvariantDef;
    # InvariantRegistry 表没有 run_command 列,close 必须照样成功(丢弃该字段)。
    db = {"MARSHAL_DB": f"sqlite:///{tmp_path/'rc.db'}"}
    _run(["ratchet-open", "--desc", "d", "--root-cause", "determinism-gap",
          "--escape-id", "esc-rc"], env=db)
    inv = json.dumps({
        "id": "det.bare_pow_literal", "domain": "determinism", "spec_ref": "M-B",
        "executor_kind": "proptest", "location_repo": "node",
        "location_path": "execution/src/pvm_executor.rs",
        "location_test": "prop_bare_pow_literal_blocked", "severity": "high",
        "run_command": ["cargo", "test", "-p", "cowboy-execution",
                        "prop_bare_pow_literal_blocked", "--", "--exact"]})
    cl = _run(["ratchet-close", "--escape-id", "esc-rc",
               "--spawned-check", "det.bare_pow_literal", "--inv-json", inv], env=db)
    assert cl.returncode == 0, cl.stderr
    assert json.loads(cl.stdout)["ok"] is True


def test_ratchet_close_without_spawned_check_fails(tmp_path):
    db = {"MARSHAL_DB": f"sqlite:///{tmp_path/'t2.db'}"}
    _run(["ratchet-open", "--desc", "d", "--root-cause", "c",
          "--escape-id", "esc-t2"], env=db)
    cl = _run(["ratchet-close", "--escape-id", "esc-t2",
               "--spawned-check", "", "--inv-json", "{}"], env=db)
    assert cl.returncode == 1
    assert "error" in json.loads(cl.stdout)


def test_gate_record_persists_run(tmp_path):
    db = {"MARSHAL_DB": f"sqlite:///{tmp_path/'g.db'}"}
    ev = json.dumps([{"name": "invariants", "outcome": "pass", "evidence_ref": "inv-x"}])
    proc = _run(["gate-record", "--change-ref", "abc123", "--verdict", "pass",
                 "--evidence-json", ev], env=db)
    assert proc.returncode == 0, proc.stderr
    assert isinstance(json.loads(proc.stdout)["run_id"], int)


def test_setup_creates_symlink(tmp_path):
    home = tmp_path / "fakehome"
    home.mkdir()
    proc = _run(["setup"], env={"HOME": str(home)})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    link = home / ".claude" / "skills" / "marshal"
    assert link.is_symlink()
    assert out["ok"] is True
    assert out["import_ok"] is True
