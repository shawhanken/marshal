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
    proc = _run(["invariants", "--repo", "wallet", "--paths", "src/tx/encode.js"])
    out = json.loads(proc.stdout)
    assert "contract.tx_encoding_roundtrip" in [i["id"] for i in out]


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


def test_ratchet_close_without_spawned_check_fails(tmp_path):
    db = {"MARSHAL_DB": f"sqlite:///{tmp_path/'t2.db'}"}
    _run(["ratchet-open", "--desc", "d", "--root-cause", "c",
          "--escape-id", "esc-t2"], env=db)
    cl = _run(["ratchet-close", "--escape-id", "esc-t2",
               "--spawned-check", "", "--inv-json", "{}"], env=db)
    assert cl.returncode == 1
    assert "error" in json.loads(cl.stdout)
