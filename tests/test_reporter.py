import json
from marshal_core.executor import reporter


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def read(self):
        return json.dumps(self._p).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_brain(monkeypatch, invariants, posted):
    def fake_urlopen(req, timeout=0):
        url = req.full_url
        if url.endswith("/plan"):
            return _FakeResp({"job_id": "inv-sha1", "invariants": invariants})
        if url.endswith("/results"):
            posted["body"] = json.loads(req.data.decode())
            return _FakeResp({"verdict": "block"})
        raise AssertionError(url)
    monkeypatch.setattr(reporter.urllib.request, "urlopen", fake_urlopen)


class _Proc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_reporter_runs_planned_invariants(monkeypatch):
    posted = {}
    _patch_brain(monkeypatch, [
        {"invariant_id": "a", "run_command": ["true"], "location_repo": "node", "executor_kind": "command"},
        {"invariant_id": "b", "run_command": ["false"], "location_repo": "node", "executor_kind": "command"},
    ], posted)

    def fake_run(argv, capture_output=True, text=True, timeout=None):
        return _Proc(returncode=0 if argv == ["true"] else 1)

    monkeypatch.setattr(reporter.subprocess, "run", fake_run)

    rc = reporter.run(brain_url="http://brain", repo="node",
                      change_ref="sha1", diff_paths=["x"])
    assert rc == 0
    body = posted["body"]
    assert body["job_id"] == "inv-sha1"
    results = {r["invariant_id"]: r["passed"] for r in body["payload"]["results"]}
    assert results == {"a": True, "b": False}
    assert body["status"] == "ok"


def test_cross_repo_invariant_is_not_run_and_degrades(monkeypatch):
    posted = {}
    executed = []
    _patch_brain(monkeypatch, [
        {"invariant_id": "local.check", "run_command": ["true"],
         "location_repo": "wallet", "executor_kind": "command"},
        {"invariant_id": "contract.tx_encoding_roundtrip",
         "run_command": ["cargo", "test", "-p", "cowboy-types"],
         "location_repo": "node"},
    ], posted)

    def fake_run(argv, capture_output=True, text=True, timeout=None):
        executed.append(argv)
        return _Proc(returncode=0)

    monkeypatch.setattr(reporter.subprocess, "run", fake_run)
    reporter.run(brain_url="http://brain", repo="wallet",
                 change_ref="sha1", diff_paths=["x"])

    assert executed == [["true"]]          # the node-repo command never ran here
    body = posted["body"]
    assert body["status"] == "degraded"
    not_run = {n["invariant_id"]: n["reason"] for n in body["payload"]["not_run"]}
    assert "contract.tx_encoding_roundtrip" in not_run
    assert "node" in not_run["contract.tx_encoding_roundtrip"]
    assert [r["invariant_id"] for r in body["payload"]["results"]] == ["local.check"]


def test_missing_location_repo_is_not_run(monkeypatch):
    posted = {}
    _patch_brain(monkeypatch, [
        {"invariant_id": "a", "run_command": ["true"]},
    ], posted)
    monkeypatch.setattr(reporter.subprocess, "run",
                        lambda *a, **kw: _Proc(returncode=0))
    reporter.run(brain_url="http://brain", repo="node",
                 change_ref="sha1", diff_paths=["x"])
    body = posted["body"]
    assert body["status"] == "degraded"
    assert body["payload"]["not_run"][0]["invariant_id"] == "a"


def test_zero_tests_with_exit_zero_is_not_run(monkeypatch):
    posted = {}
    _patch_brain(monkeypatch, [
        {"invariant_id": "econ.fee_conservation",
         "run_command": ["cargo", "test", "nope", "--", "--exact"],
         "location_repo": "node", "executor_kind": "proptest"},
    ], posted)
    out = "running 0 tests\n\ntest result: ok. 0 passed; 0 failed; 0 ignored\n"
    monkeypatch.setattr(reporter.subprocess, "run",
                        lambda *a, **kw: _Proc(returncode=0, stdout=out))
    reporter.run(brain_url="http://brain", repo="node",
                 change_ref="sha1", diff_paths=["x"])
    body = posted["body"]
    assert body["status"] == "degraded"
    assert body["payload"]["results"] == []
    assert body["payload"]["not_run"][0]["invariant_id"] == "econ.fee_conservation"


def test_cargo_test_with_real_passes_still_passes(monkeypatch):
    posted = {}
    out = "running 2 tests\n..\ntest result: ok. 2 passed; 0 failed; 0 ignored\n"
    _patch_brain(monkeypatch, [
        {"invariant_id": "econ.fee_conservation",
         "run_command": ["cargo", "test", "fee"],
         "location_repo": "node", "executor_kind": "proptest"},
    ], posted)
    monkeypatch.setattr(reporter.subprocess, "run",
                        lambda *a, **kw: _Proc(returncode=0, stdout=out))
    reporter.run(brain_url="http://brain", repo="node",
                 change_ref="sha1", diff_paths=["x"])
    body = posted["body"]
    assert body["status"] == "ok"
    assert body["payload"]["results"][0]["passed"] is True


def test_timeout_is_not_run_and_degrades(monkeypatch):
    posted = {}
    _patch_brain(monkeypatch, [
        {"invariant_id": "slow.check", "run_command": ["sleep", "9999"],
         "location_repo": "node", "executor_kind": "command"},
    ], posted)

    def fake_run(argv, capture_output=True, text=True, timeout=None):
        raise reporter.subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    monkeypatch.setattr(reporter.subprocess, "run", fake_run)
    reporter.run(brain_url="http://brain", repo="node",
                 change_ref="sha1", diff_paths=["x"])
    body = posted["body"]
    assert body["status"] == "degraded"
    assert body["payload"]["not_run"][0]["invariant_id"] == "slow.check"
    assert "timed out" in body["payload"]["not_run"][0]["reason"]


def test_unknown_executor_kind_is_not_run(monkeypatch):
    posted = {}
    _patch_brain(monkeypatch, [
        {"invariant_id": "a", "run_command": ["cargo", "test", "nope"],
         "location_repo": "node", "executor_kind": "typo"},
    ], posted)
    executed = []
    monkeypatch.setattr(reporter.subprocess, "run",
                        lambda *a, **kw: executed.append(a) or _Proc(returncode=0))
    reporter.run(brain_url="http://brain", repo="node",
                 change_ref="sha1", diff_paths=["x"])
    assert executed == []
    assert posted["body"]["status"] == "degraded"
    assert "unsupported" in posted["body"]["payload"]["not_run"][0]["reason"]


def test_malformed_plan_is_reported_degraded(monkeypatch):
    posted = {}
    def fake_urlopen(req, timeout=0):
        url = req.full_url
        if url.endswith("/plan"):
            return _FakeResp({"job_id": "inv-sha1",
                              "invariants": [{"invariant_id": "a",
                                               "location_repo": "node"}]})
        posted["body"] = json.loads(req.data.decode())
        return _FakeResp({"verdict": "escalate"})
    monkeypatch.setattr(reporter.urllib.request, "urlopen", fake_urlopen)
    reporter.run(brain_url="http://brain", repo="node",
                 change_ref="sha1", diff_paths=["x"])
    assert posted["body"]["status"] == "degraded"
    assert posted["body"]["payload"]["results"] == []
    assert posted["body"]["payload"]["not_run"][0]["invariant_id"] == "a"


def test_total_timeout_marks_remaining_invariants_not_run(monkeypatch):
    posted = {}
    _patch_brain(monkeypatch, [
        {"invariant_id": "a", "run_command": ["true"], "location_repo": "node", "executor_kind": "command"},
        {"invariant_id": "b", "run_command": ["true"], "location_repo": "node", "executor_kind": "command"},
    ], posted)
    monkeypatch.setattr(reporter, "_TOTAL_TIMEOUT_SEC", 0)
    executed = []
    monkeypatch.setattr(reporter.subprocess, "run",
                        lambda *a, **kw: executed.append(a) or _Proc(returncode=0))
    reporter.run(brain_url="http://brain", repo="node",
                 change_ref="sha1", diff_paths=["x"])
    assert executed == []
    assert posted["body"]["status"] == "degraded"
    assert {x["invariant_id"] for x in posted["body"]["payload"]["not_run"]} == {"a", "b"}


def test_reporter_preserves_comma_paths_and_labels(monkeypatch):
    posted = {}
    _patch_brain(monkeypatch, [], posted)
    encoded = reporter.base64.b64encode(
        b"src/a,b.rs\0docs/normal.md\0").decode()
    assert reporter._decode_diff_paths(encoded) == ["src/a,b.rs", "docs/normal.md"]
    assert reporter._parse_labels('[{"name":"security"}, {"name":"cip"}]') == [
        "security", "cip"]
    reporter.run(brain_url="http://brain", repo="node",
                 change_ref="sha1", diff_paths=["src/a,b.rs"],
                 labels=["security"])
    assert posted["body"]["payload"]["results"] == []


def test_reporter_rejects_malformed_lossless_paths():
    import pytest
    with pytest.raises(ValueError, match="NUL terminated"):
        reporter._decode_diff_paths(reporter.base64.b64encode(b"src/x.rs").decode())
