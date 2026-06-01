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
