from marshal_core.knowledge.store import Store

STABLE_KEYS = {"title", "repo", "pr", "tier", "cip", "dimensions", "severity",
               "findings_total", "top_findings", "invariants_pass", "invariants_total",
               "changed_files", "comment_url", "headline"}


def test_summary_always_has_stable_keys():
    for ev in (None, {}, {"gates": {}}, {"gates": "not-a-dict"}):
        s = Store.inbox_summary(ev)
        assert set(s.keys()) == STABLE_KEYS
        assert s["dimensions"] == [] and s["top_findings"] == []
        assert s["title"] is None and s["tier"] is None and s["severity"] is None


def test_summary_repo_pr_becomes_title():
    ev = {"gates": {"repo": "marshal", "pr": 38, "tier": "mid",
                    "lenses": {"completed": ["correctness", "spec"]},
                    "findings": [{"severity": "high", "title": "schema not migrated"},
                                 {"severity": "high", "title": "second thing"}]}}
    s = Store.inbox_summary(ev)
    assert s["title"] == "marshal #38"
    assert s["repo"] == "marshal" and s["pr"] == 38
    assert s["dimensions"] == ["correctness", "spec"]
    assert s["findings_total"] == 2
    assert s["top_findings"][0] == {"severity": "high", "title": "schema not migrated"}


def test_summary_without_repo_uses_top_finding_title_and_review_quorum():
    ev = {"gates": {"tier": "mid",
                    "lenses": ["correctness", "spec", "cross-repo"],
                    "closure": {"changed_files": 9},
                    "review_quorum": {"verdict": "escalate", "confirmed_high": 4, "advisory": 1},
                    "findings": [{"severity": "high", "title": "lossy path transport"}]}}
    s = Store.inbox_summary(ev)
    assert s["repo"] is None and s["pr"] is None
    assert s["title"] == "lossy path transport"          # falls back to top finding
    assert s["severity"] == "4 high · 1 advisory"         # from review_quorum
    assert s["dimensions"] == ["correctness", "spec", "cross-repo"]
    assert s["changed_files"] == 9


def test_summary_severity_counted_from_findings_when_no_quorum():
    ev = {"gates": {"findings": [{"severity": "high"}, {"severity": "high"},
                                 {"severity": "medium"}]}}
    s = Store.inbox_summary(ev)
    assert s["severity"] == "2 high · 1 medium"
    assert s["findings_total"] == 3


def test_summary_rejects_non_http_comment_url():
    assert Store.inbox_summary({"gates": {"comment_url": "javascript:alert(1)"}})["comment_url"] is None
    assert Store.inbox_summary({"gates": {"comment_url": "https://x/y"}})["comment_url"] == "https://x/y"


def test_summary_flat_fixture_still_works():
    ev = {"tier": "high", "cip": "CIP-13", "repo": "node",
          "invariants_run": 10, "invariants_pass": 9}
    s = Store.inbox_summary(ev)
    assert s["tier"] == "high" and s["cip"] == "CIP-13" and s["repo"] == "node"
    assert s["invariants_pass"] == 9 and s["invariants_total"] == 10


def test_summary_invariants_map_ratio():
    s = Store.inbox_summary({"gates": {"invariants": {"a": "pass", "b": "fail"}}})
    assert s["invariants_pass"] == 1 and s["invariants_total"] == 2


def test_summary_reads_nested_review_and_parses_pr_from_comment_url():
    ev = {"gates": {
        "review": {"tier": "high", "lenses": ["correctness", "spec", "cross-repo"]},
        "comment_url": "https://github.com/cowboyinc/node/pull/1307#issuecomment-5257380871",
    }}
    s = Store.inbox_summary(ev)
    assert s["tier"] == "high"                          # from gates.review.tier
    assert s["dimensions"] == ["correctness", "spec", "cross-repo"]
    assert s["repo"] == "node" and s["pr"] == 1307      # parsed from comment_url
    assert s["title"] == "node #1307"


def test_summary_confirmed_findings_fallback_counts():
    ev = {"gates": {"review": {"confirmed_findings": ["almanax-leak", "state-fork"]}}}
    s = Store.inbox_summary(ev)
    assert s["findings_total"] == 2
    assert s["top_findings"][0]["title"] == "almanax-leak"


def test_summary_parses_gates_stored_as_json_string():
    import json as _json
    ev = {"gates": _json.dumps({"repo": "node", "pr": 42, "tier": "mid"})}
    s = Store.inbox_summary(ev)
    assert s["title"] == "node #42" and s["tier"] == "mid"


def test_summary_pr_as_object_and_marker_url():
    ev = {"gates": {"repo": "cbfs", "pr": {"number": 134, "title": "x"}}}
    assert Store.inbox_summary(ev)["title"] == "cbfs #134"
    ev2 = {"gates": {"marker_url": "https://github.com/o/cbss/pull/9#issuecomment-1"}}
    s2 = Store.inbox_summary(ev2)
    assert s2["title"] == "cbss #9" and s2["comment_url"].endswith("issuecomment-1")


def test_list_needs_human_includes_summary(db_session):
    s = Store(db_session)
    s.record_gate_run(change_ref="node#2", job_id="j2", verdict="needs_human",
                      evidence={"gates": {"repo": "marshal", "pr": 7, "tier": "low"}})
    assert s.list_needs_human()[0]["summary"]["title"] == "marshal #7"
