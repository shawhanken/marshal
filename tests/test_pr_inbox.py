from marshal_core import pr_inbox
from marshal_core.knowledge.store import Store


def test_bound_repos_default(monkeypatch):
    monkeypatch.delenv("MARSHAL_REPOS", raising=False)
    repos = pr_inbox.bound_repos()
    assert ("cowboyinc", "node") in repos and ("shawhanken", "marshal") in repos


def test_bound_repos_from_env(monkeypatch):
    monkeypatch.setenv("MARSHAL_REPOS", "acme/foo, acme/bar")
    assert pr_inbox.bound_repos() == [("acme", "foo"), ("acme", "bar")]


def test_eligibility_conflict_and_ci():
    assert pr_inbox.eligibility("dirty", "success") == (False, "merge conflict")
    assert pr_inbox.eligibility("clean", "failure") == (False, "CI failing")
    assert pr_inbox.eligibility("clean", "pending") == (False, "CI pending")   # in-flight -> not merge-ready
    assert pr_inbox.eligibility("clean", "success") == (True, None)
    assert pr_inbox.eligibility(None, None) == (True, None)
    assert pr_inbox.eligibility("blocked", "pending") == (False, "CI pending")


def test_ci_from_check_runs():
    assert pr_inbox._ci_from_check_runs([{"status": "completed", "conclusion": "failure"}]) == "failure"
    assert pr_inbox._ci_from_check_runs(
        [{"status": "completed", "conclusion": "success"},
         {"status": "completed", "conclusion": "success"}]) == "success"
    assert pr_inbox._ci_from_check_runs([{"status": "in_progress", "conclusion": None}]) == "pending"
    assert pr_inbox._ci_from_check_runs([]) is None
    # any failing run wins, even mixed with successes
    assert pr_inbox._ci_from_check_runs(
        [{"status": "completed", "conclusion": "success"},
         {"status": "completed", "conclusion": "timed_out"}]) == "failure"
    assert pr_inbox._ci_from_check_runs([{"status": "completed", "conclusion": "startup_failure"}]) == "failure"
    # skipped / neutral are not blocking -> clean success
    assert pr_inbox._ci_from_check_runs(
        [{"status": "completed", "conclusion": "success"},
         {"status": "completed", "conclusion": "skipped"},
         {"status": "completed", "conclusion": "neutral"}]) == "success"


def test_marshal_deep_marker_last_wins():
    assert pr_inbox._marshal_deep_marker([]) is None
    assert pr_inbox._marshal_deep_marker(["nothing here"]) is None
    assert pr_inbox._marshal_deep_marker(
        ["verdict...\n<!-- marshal-deep sha=abc1234 -->",
         "later\n<!-- marshal-deep sha=def5678 -->"]) == "def5678"


def test_is_cip10():
    assert pr_inbox._is_cip10("Implement CIP-10 container registry") is True
    assert pr_inbox._is_cip10("Add Container Registry endpoint") is True
    assert pr_inbox._is_cip10("CIP-3 fee model tweak") is False
    assert pr_inbox._is_cip10("CIP-100 something") is False


def _wire(monkeypatch, prs, mergeable, comments_by_pr):
    """Stub the four GitHub seams. `comments_by_pr` maps pr# -> list of comment bodies."""
    monkeypatch.setattr(pr_inbox, "list_open_prs",
                        lambda o, r, per_page=30: prs if r == "node" else [])
    monkeypatch.setattr(pr_inbox, "pr_detail",
                        lambda o, r, n: {"mergeable_state": mergeable.get(n, "clean")})
    monkeypatch.setattr(pr_inbox, "commit_status", lambda o, r, sha: "success")
    monkeypatch.setattr(pr_inbox, "pr_comments",
                        lambda o, r, n: comments_by_pr.get(n, []))


def _marker(sha):
    return [f"Marshal verdict...\n<!-- marshal-deep sha={sha} -->"]


def test_build_inbox_joins_prs_eligibility_and_last_review(db_session, monkeypatch):
    # a prior deep review of node#7 at an OLD head -> should show as stale
    Store(db_session).record_gate_run(change_ref="aaa1111", job_id="j", verdict="escalate",
                                      evidence={"gates": {"repo": "node", "pr": 7}})
    prs = [
        {"number": 7, "title": "fix A", "html_url": "u7", "updated_at": "2026-08-14T02:00:00Z",
         "draft": False, "head": {"sha": "ccc3333"}},
        {"number": 9, "title": "fix B", "html_url": "u9", "updated_at": "2026-08-14T05:00:00Z",
         "draft": True, "head": {"sha": "h9"}},
    ]
    _wire(monkeypatch, prs, {7: "dirty", 9: "clean"}, {7: _marker("aaa1111")})

    inbox = pr_inbox.build_inbox(db_session, repos=[("cowboyinc", "node")])
    assert [p["number"] for p in inbox] == [9, 7]          # sorted by updated_at desc
    p9, p7 = inbox[0], inbox[1]
    assert p9["draft"] is True and p9["eligible"] is True   # drafts are eligible
    assert p7["eligible"] is False and p7["blocked_reason"] == "merge conflict"
    assert p7["last_review"] == {"verdict": "escalate", "reviewed_head": "aaa1111", "stale": True}
    assert p9["last_review"] is None                        # never deep-reviewed (no marker)
    assert p7["title"] == "fix A" and p7["url"] == "u7"


def test_build_inbox_verdict_enriched_from_matching_sha(db_session, monkeypatch):
    s = Store(db_session)
    # two reviews of node#7; the marker points at the newer head -> newer verdict shown
    s.record_gate_run(change_ref="aaa1111", job_id="j1", verdict="escalate",
                      evidence={"gates": {"repo": "node", "pr": 7}})
    s.record_gate_run(change_ref="bbb2222", job_id="j2", verdict="needs_human",
                      evidence={"gates": {"repo": "node", "pr": 7}})
    prs = [{"number": 7, "title": "t", "html_url": "u", "updated_at": "2026-08-14T00:00:00Z",
            "draft": False, "head": {"sha": "bbb2222"}}]
    _wire(monkeypatch, prs, {}, {7: _marker("bbb2222")})
    lr = pr_inbox.build_inbox(db_session, repos=[("cowboyinc", "node")])[0]["last_review"]
    assert lr["verdict"] == "needs_human"        # verdict at the marker's sha
    assert lr["reviewed_head"] == "bbb2222"
    assert lr["stale"] is False                  # reviewed at the current head


def test_build_inbox_marker_at_current_head_is_pending(db_session, monkeypatch):
    # deep marker at the PR's CURRENT head -> 待处理 (skip; pr-sweep sha-state)
    Store(db_session).record_gate_run(change_ref="bbb2222", job_id="j", verdict="needs_human",
                                      evidence={"gates": {"repo": "node", "pr": 5}})
    prs = [{"number": 5, "title": "t", "html_url": "u", "updated_at": "2026-08-14T00:00:00Z",
            "draft": False, "head": {"sha": "bbb2222"}}]
    _wire(monkeypatch, prs, {}, {5: _marker("bbb2222")})
    p = pr_inbox.build_inbox(db_session, repos=[("cowboyinc", "node")])[0]
    assert p["eligible"] is False
    assert "no new commits" in p["blocked_reason"]
    assert p["last_review"]["stale"] is False


def test_build_inbox_no_marker_is_eligible(db_session, monkeypatch):
    # a local DB gate_run but NO marker on the PR -> never deep-reviewed of record -> eligible
    Store(db_session).record_gate_run(change_ref="bbb2222", job_id="j", verdict="pass",
                                      evidence={"gates": {"repo": "node", "pr": 5}})
    prs = [{"number": 5, "title": "t", "html_url": "u", "updated_at": "2026-08-14T00:00:00Z",
            "draft": False, "head": {"sha": "bbb2222"}}]
    _wire(monkeypatch, prs, {}, {})   # no comments -> no marker
    p = pr_inbox.build_inbox(db_session, repos=[("cowboyinc", "node")])[0]
    assert p["eligible"] is True
    assert p["last_review"] is None


def test_build_inbox_stale_marker_stays_eligible(db_session, monkeypatch):
    # marker at an OLD head -> code changed -> still eligible (re-review the new head)
    Store(db_session).record_gate_run(change_ref="aaa1111", job_id="j", verdict="escalate",
                                      evidence={"gates": {"repo": "node", "pr": 5}})
    prs = [{"number": 5, "title": "t", "html_url": "u", "updated_at": "2026-08-14T00:00:00Z",
            "draft": False, "head": {"sha": "ccc3333"}}]
    _wire(monkeypatch, prs, {}, {5: _marker("aaa1111")})
    p = pr_inbox.build_inbox(db_session, repos=[("cowboyinc", "node")])[0]
    assert p["eligible"] is True
    assert p["last_review"]["stale"] is True


def test_build_inbox_passed_pr_with_new_commits_is_eligible(db_session, monkeypatch):
    # pure sha-state: last verdict was pass but the head moved -> re-review (sweep-aligned)
    Store(db_session).record_gate_run(change_ref="aaa1111", job_id="j", verdict="pass",
                                      evidence={"gates": {"repo": "node", "pr": 5}})
    prs = [{"number": 5, "title": "t", "html_url": "u", "updated_at": "2026-08-14T00:00:00Z",
            "draft": False, "head": {"sha": "ccc3333"}}]
    _wire(monkeypatch, prs, {}, {5: _marker("aaa1111")})
    p = pr_inbox.build_inbox(db_session, repos=[("cowboyinc", "node")])[0]
    assert p["eligible"] is True
    assert p["last_review"] == {"verdict": "pass", "reviewed_head": "aaa1111", "stale": True}


def test_build_inbox_pending_ci_is_pending(db_session, monkeypatch):
    prs = [{"number": 5, "title": "t", "html_url": "u", "updated_at": "2026-08-14T00:00:00Z",
            "draft": False, "head": {"sha": "h"}}]
    _wire(monkeypatch, prs, {}, {})
    monkeypatch.setattr(pr_inbox, "commit_status", lambda o, r, sha: "pending")
    p = pr_inbox.build_inbox(db_session, repos=[("cowboyinc", "node")])[0]
    assert p["eligible"] is False and p["blocked_reason"] == "CI pending"


def test_build_inbox_cip10_avoidance(db_session, monkeypatch):
    prs = [{"number": 5, "title": "Implement CIP-10 registry", "html_url": "u",
            "updated_at": "2026-08-14T00:00:00Z", "draft": False, "head": {"sha": "h"}}]
    _wire(monkeypatch, prs, {}, {})
    p = pr_inbox.build_inbox(db_session, repos=[("cowboyinc", "node")])[0]
    assert p["eligible"] is False and p["blocked_reason"] == "CIP-10 avoidance"


def test_build_inbox_verdict_enriched_by_sha_when_evidence_lacks_repo_pr(db_session, monkeypatch):
    # sweep gate-record rows often carry no repo/pr in evidence -> match by change_ref==sha
    Store(db_session).record_gate_run(change_ref="bbb2222", job_id="j", verdict="block",
                                      evidence={})   # no repo/pr
    prs = [{"number": 5, "title": "t", "html_url": "u", "updated_at": "2026-08-14T00:00:00Z",
            "draft": False, "head": {"sha": "ccc3333"}}]
    _wire(monkeypatch, prs, {}, {5: _marker("bbb2222")})
    p = pr_inbox.build_inbox(db_session, repos=[("cowboyinc", "node")])[0]
    assert p["last_review"] == {"verdict": "block", "reviewed_head": "bbb2222", "stale": True}
