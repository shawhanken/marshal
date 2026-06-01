from marshal_core.executor import reporter


class _FakeResp:
    def __init__(self, payload): self._p = payload
    def read(self): import json; return json.dumps(self._p).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_reporter_runs_planned_invariants(monkeypatch):
    posted = {}

    def fake_urlopen(req, timeout=0):
        import json
        url = req.full_url
        if url.endswith("/plan"):
            return _FakeResp({"job_id": "inv-sha1", "invariants": [
                {"invariant_id": "a", "run_command": ["true"]},
                {"invariant_id": "b", "run_command": ["false"]},
            ]})
        if url.endswith("/results"):
            posted["body"] = json.loads(req.data.decode())
            return _FakeResp({"verdict": "block"})
        raise AssertionError(url)

    def fake_run(argv, capture_output=True, text=True):
        class R: pass
        r = R(); r.returncode = 0 if argv == ["true"] else 1
        return r

    monkeypatch.setattr(reporter.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(reporter.subprocess, "run", fake_run)

    rc = reporter.run(brain_url="http://brain", repo="node",
                      change_ref="sha1", diff_paths=["x"])
    assert rc == 0
    body = posted["body"]
    assert body["job_id"] == "inv-sha1"
    results = {r["invariant_id"]: r["passed"] for r in body["payload"]["results"]}
    assert results == {"a": True, "b": False}
    assert body["status"] == "ok"
