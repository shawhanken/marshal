from marshal_core.adapters.github import parse_pull_request_event, build_check_run


def test_parse_pull_request_event():
    payload = {
        "action": "synchronize",
        "repository": {"name": "node"},
        "pull_request": {"head": {"sha": "abc123"}, "user": {"login": "alice"},
                         "labels": [{"name": "cip"}]},
    }
    ev = parse_pull_request_event(
        payload, diff_paths=["execution/src/execution/transaction.rs"])
    assert ev.repo == "node" and ev.change_ref == "abc123"
    assert ev.labels == ["cip"]
    assert "execution/src/execution/transaction.rs" in ev.diff_paths


def test_parse_ignores_smuggled_diff_paths_field():
    payload = {
        "action": "synchronize",
        "repository": {"name": "node"},
        "pull_request": {"head": {"sha": "abc123"}, "user": {"login": "alice"},
                         "labels": []},
        "_diff_paths": ["execution/src/execution/transaction.rs"],
    }
    ev = parse_pull_request_event(payload, diff_paths=[])
    assert ev.diff_paths == []


def test_check_run_is_shadow_neutral():
    from marshal_core.contracts import GateDecision
    d = GateDecision(change_ref="abc123", tier="high",
                     gates=[{"name": "invariants", "outcome": "fail", "evidence_ref": "j"}],
                     verdict="block")
    cr = build_check_run(d, shadow=True)
    assert cr["conclusion"] == "neutral"
    assert "block" in cr["output"]["summary"]
    assert cr["head_sha"] == "abc123"
