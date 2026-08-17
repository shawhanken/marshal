from marshal_core.knowledge.store import Store
from marshal_core import github_backfill as gbf
import marshal_core.worker as worker


def test_maybe_backfill_noop_without_token(db_session, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert worker.maybe_backfill(db_session) == 0


def test_maybe_backfill_runs_when_token_set(db_session, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setattr(gbf, "fetch_pulls",
                        lambda org, repo, sha: [{"number": 42}] if repo == "node" else [])
    s = Store(db_session)
    s.record_gate_run(change_ref="known", job_id="k", verdict="escalate",
                      evidence={"gates": {"comment_url": "https://github.com/cowboyinc/node/pull/1#c"}})
    s.record_gate_run(change_ref="baresha", job_id="b", verdict="escalate",
                      evidence={"gates": {"tier": "mid"}})
    assert worker.maybe_backfill(db_session) == 1
    row = next(r for r in s.list_needs_human() if r["change_ref"] == "baresha")
    assert row["summary"]["title"] == "node #42"


def test_maybe_backfill_swallows_errors(db_session, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setattr(gbf, "backfill",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert worker.maybe_backfill(db_session) == 0   # swallowed, worker keeps running


def test_inbox_summary_reads_backfill():
    ev = {"gates": {"tier": "mid"}, "_backfill": {"repo": "node", "pr": 999}}
    s = Store.inbox_summary(ev)
    assert s["repo"] == "node" and s["pr"] == 999 and s["title"] == "node #999"


def test_candidate_repos_from_links(db_session):
    s = Store(db_session)
    s.record_gate_run(change_ref="a", job_id="a", verdict="escalate",
                      evidence={"gates": {"comment_url": "https://github.com/cowboyinc/node/pull/7#c"}})
    s.record_gate_run(change_ref="b", job_id="b", verdict="pass",
                      evidence={"gates": {"marker_url": "https://github.com/shawhanken/marshal/pull/3"}})
    cands = set(gbf.candidate_repos(db_session))
    assert ("cowboyinc", "node") in cands and ("shawhanken", "marshal") in cands


def test_backfill_writes_repo_pr_and_inbox_reads_it(db_session):
    s = Store(db_session)
    s.record_gate_run(change_ref="known", job_id="k", verdict="escalate",
                      evidence={"gates": {"comment_url": "https://github.com/cowboyinc/node/pull/7#c"}})
    s.record_gate_run(change_ref="baresha123", job_id="bare", verdict="escalate",
                      evidence={"gates": {"tier": "mid"}})

    def fake_fetch(org, repo, sha):
        if (org, repo) == ("cowboyinc", "node") and sha == "baresha123":
            return [{"number": 1307}]
        return []

    assert gbf.backfill(db_session, fetch=fake_fetch) == 1
    row = next(r for r in s.list_needs_human() if r["change_ref"] == "baresha123")
    assert row["summary"]["title"] == "node #1307"


def test_backfill_skips_rows_that_already_have_identity(db_session):
    s = Store(db_session)
    s.record_gate_run(change_ref="x", job_id="x", verdict="escalate",
                      evidence={"gates": {"repo": "node", "pr": 5,
                                          "comment_url": "https://github.com/o/node/pull/5"}})
    calls = []

    def fake_fetch(org, repo, sha):
        calls.append(sha)
        return []

    assert gbf.backfill(db_session, fetch=fake_fetch) == 0
    assert calls == []   # never queried GitHub for an already-identified row


def test_format_verdict_comment():
    body = gbf.format_verdict_comment({
        "verdict": "needs_human", "summary": "risky change",
        "findings": [{"severity": "high", "title": "leak"}, {"severity": "low", "title": "nit"}]})
    assert "needs_human" in body and "risky change" in body
    assert "**high** — leak" in body and "advisory" in body.lower()


def test_resolve_pr_prefers_job_repo(db_session):
    s = Store(db_session)
    s.record_gate_run(change_ref="a", job_id="a", verdict="escalate",
                      evidence={"gates": {"comment_url": "https://github.com/cowboyinc/node/pull/1"}})
    s.record_gate_run(change_ref="b", job_id="b", verdict="escalate",
                      evidence={"gates": {"comment_url": "https://github.com/cowboyinc/cbfs/pull/2"}})
    seen = []
    def fake(org, repo, sha):
        seen.append(repo)
        return [{"number": 42}] if repo == "cbfs" else []
    assert gbf.resolve_pr(db_session, "somesha", prefer_repo="cbfs", fetch=fake) == ("cowboyinc", "cbfs", 42)
    assert seen[0] == "cbfs"   # tried the preferred repo first


def test_post_deep_verdict_resolves_and_posts(db_session):
    posted = {}
    ok = gbf.post_deep_verdict(
        db_session, "sha", "node", {"verdict": "block"},
        resolver=lambda *a, **k: ("cowboyinc", "node", 7),
        poster=lambda org, repo, pr, body: posted.update(pr=pr, body=body) or True)
    assert ok and posted["pr"] == 7 and "block" in posted["body"]


def test_post_deep_verdict_noop_when_pr_unresolved(db_session):
    assert gbf.post_deep_verdict(db_session, "sha", "node", {"verdict": "pass"},
                                 resolver=lambda *a, **k: None,
                                 poster=lambda *a, **k: True) is False
