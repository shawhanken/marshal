import json
import os
import subprocess

import pytest

from marshal_core.knowledge.store import Store
from marshal_core.worker import (
    DeepReviewError,
    VERDICT_FILE,
    _deep_worktree,
    _invoke_claude,
    _parse_verdict,
    _run_deep,
    run_once,
)
from marshal_pack_cowboy.pack import CowboyPack


def _write(tmp_path, obj):
    p = tmp_path / VERDICT_FILE
    p.write_text(json.dumps(obj))
    return str(p)


def test_parse_verdict_valid(tmp_path):
    path = _write(tmp_path, {"verdict": "needs_human", "summary": "s",
                             "findings": ["f1"], "invariants_run": 5, "invariants_pass": 5})
    v = _parse_verdict(path)
    assert v["verdict"] == "needs_human"
    assert v["findings"] == ["f1"]


def test_parse_verdict_missing_file_raises(tmp_path):
    with pytest.raises(DeepReviewError, match="not written"):
        _parse_verdict(str(tmp_path / VERDICT_FILE))


def test_parse_verdict_bad_json_raises(tmp_path):
    p = tmp_path / VERDICT_FILE
    p.write_text("{not json")
    with pytest.raises(DeepReviewError, match="unparseable"):
        _parse_verdict(str(p))


def test_parse_verdict_invalid_verdict_value_raises(tmp_path):
    path = _write(tmp_path, {"verdict": "lgtm"})
    with pytest.raises(DeepReviewError, match="invalid verdict"):
        _parse_verdict(path)


def _make_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / "f.txt").write_text("hi")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)
    sha = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return sha


def test_deep_worktree_creates_and_tears_down(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    sha = _make_repo(ws / "node")
    monkeypatch.setenv("MARSHAL_WORKSPACE", str(ws))
    # Use a stable non-/tmp path for worktrees (default ~/.marshal/worktrees)
    wt_base = tmp_path / "stable_wts"
    wt_base.mkdir()
    monkeypatch.setenv("MARSHAL_WORKTREE_BASE", str(wt_base))

    seen = {}
    with _deep_worktree("node", sha) as wt:
        seen["wt"] = wt
        assert os.path.isdir(wt)
        assert os.path.exists(os.path.join(wt, "f.txt"))  # checked out at the ref
        assert str(wt_base) in wt                          # uses the configured base
    assert not os.path.isdir(seen["wt"])


def test_deep_worktree_self_heals_stale_path(tmp_path, monkeypatch):
    from marshal_core.worker import _worktree_path
    ws = tmp_path / "ws"
    ws.mkdir()
    sha = _make_repo(ws / "node")
    monkeypatch.setenv("MARSHAL_WORKSPACE", str(ws))
    monkeypatch.setenv("MARSHAL_WORKTREE_BASE", str(tmp_path / "wts"))
    # simulate a stale worktree left by a hard-crashed prior run at the deterministic path
    stale = _worktree_path("node", sha)
    os.makedirs(os.path.dirname(stale), exist_ok=True)
    subprocess.run(["git", "-C", str(ws / "node"), "worktree", "add", "--detach", stale, sha],
                   check=True, capture_output=True, text=True)
    assert os.path.isdir(stale)
    # a new deep_worktree on the SAME ref must self-heal and succeed (not fail on 'exists')
    with _deep_worktree("node", sha) as wt:
        assert wt == stale
        assert os.path.exists(os.path.join(wt, "f.txt"))
    assert not os.path.isdir(stale)   # still torn down cleanly at the end


def _fake_bin(tmp_path, name, body):
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)
    return str(p)


def test_invoke_claude_success_returns_stdout(tmp_path, monkeypatch):
    fake = _fake_bin(tmp_path, "claude_ok.sh", 'echo "did review"; exit 0')
    monkeypatch.setenv("MARSHAL_CLAUDE_BIN", fake)
    out = _invoke_claude("prompt", cwd=str(tmp_path), timeout_s=10)
    assert "did review" in out


def test_invoke_claude_nonzero_exit_raises(tmp_path, monkeypatch):
    fake = _fake_bin(tmp_path, "claude_fail.sh", 'echo "boom" 1>&2; exit 3')
    monkeypatch.setenv("MARSHAL_CLAUDE_BIN", fake)
    with pytest.raises(DeepReviewError, match="exited 3"):
        _invoke_claude("prompt", cwd=str(tmp_path), timeout_s=10)


def test_invoke_claude_timeout_raises(tmp_path, monkeypatch):
    fake = _fake_bin(tmp_path, "claude_hang.sh", 'sleep 5; exit 0')
    monkeypatch.setenv("MARSHAL_CLAUDE_BIN", fake)
    with pytest.raises(subprocess.TimeoutExpired):
        _invoke_claude("prompt", cwd=str(tmp_path), timeout_s=1)


def _deep_env(tmp_path, monkeypatch, verdict_json):
    """A fake claude that writes a verdict file into its cwd (the worktree)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    sha = _make_repo(ws / "node")
    monkeypatch.setenv("MARSHAL_WORKSPACE", str(ws))
    monkeypatch.setenv("MARSHAL_WORKTREE_BASE", str(tmp_path / "wts"))
    fake = _fake_bin(tmp_path, "claude_review.sh",
                     f"cat > {VERDICT_FILE} <<'EOF'\n{verdict_json}\nEOF\nexit 0")
    monkeypatch.setenv("MARSHAL_CLAUDE_BIN", fake)
    return sha


def test_run_deep_writes_gate_run_and_finishes(db_session, tmp_path, monkeypatch):
    sha = _deep_env(tmp_path, monkeypatch,
                    '{"verdict":"needs_human","summary":"looks risky",'
                    '"findings":["f1","f2"],"invariants_run":3,"invariants_pass":3}')
    s = Store(db_session)
    job = s.enqueue_job(change_ref=sha, repo="node", kind="deep")
    s.claim_next_job()
    _run_deep(s, job)
    done = s.get_job(job["id"])
    assert done["status"] == "done"
    assert done["result"]["verdict"] == "needs_human"
    gid = done["result"]["gate_run_id"]
    gr = s.get_gate_run(gid)
    assert gr.verdict == "needs_human"
    assert gr.evidence["source"] == "dashboard-worker"
    assert gr.evidence["job_id"] == job["id"]
    assert gr.evidence["findings"] == ["f1", "f2"]


def test_run_deep_propagates_on_bad_verdict(db_session, tmp_path, monkeypatch):
    sha = _deep_env(tmp_path, monkeypatch, '{"verdict":"lgtm"}')
    s = Store(db_session)
    job = s.enqueue_job(change_ref=sha, repo="node", kind="deep")
    s.claim_next_job()
    with pytest.raises(DeepReviewError):
        _run_deep(s, job)


def test_run_once_deep_success_end_to_end(db_session, tmp_path, monkeypatch):
    sha = _deep_env(tmp_path, monkeypatch,
                    '{"verdict":"pass","summary":"ok","findings":[],'
                    '"invariants_run":2,"invariants_pass":2}')
    s = Store(db_session)
    job = s.enqueue_job(change_ref=sha, repo="node", kind="deep")
    assert run_once(s, CowboyPack()) is True
    done = s.get_job(job["id"])
    assert done["status"] == "done"
    assert done["result"]["verdict"] == "pass"
    assert done["result"]["gate_run_id"] is not None


def test_run_once_deep_timeout_marks_failed(db_session, tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    sha = _make_repo(ws / "node")
    monkeypatch.setenv("MARSHAL_WORKSPACE", str(ws))
    monkeypatch.setenv("MARSHAL_WORKTREE_BASE", str(tmp_path / "wts"))
    hang = _fake_bin(tmp_path, "claude_hang.sh", "sleep 5; exit 0")
    monkeypatch.setenv("MARSHAL_CLAUDE_BIN", hang)
    monkeypatch.setenv("MARSHAL_DEEP_TIMEOUT_S", "1")
    s = Store(db_session)
    job = s.enqueue_job(change_ref=sha, repo="node", kind="deep")
    assert run_once(s, CowboyPack()) is True
    failed = s.get_job(job["id"])
    assert failed["status"] == "failed"          # never left 'running'
    assert "Timeout" in failed["error"] or "timed out" in failed["error"].lower()


def test_deep_worktree_rejects_repo_path_traversal(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    _make_repo(ws / "node")
    monkeypatch.setenv("MARSHAL_WORKSPACE", str(ws))
    monkeypatch.setenv("MARSHAL_WORKTREE_BASE", str(tmp_path / "wts"))
    with pytest.raises(DeepReviewError, match="escapes workspace"):
        with _deep_worktree("../evil", "HEAD"):
            pass


def test_parse_verdict_non_object_json_raises(tmp_path):
    p = tmp_path / VERDICT_FILE
    p.write_text('["not","an","object"]')
    with pytest.raises(DeepReviewError, match="not a JSON object"):
        _parse_verdict(str(p))


def test_invoke_claude_passes_permission_mode_and_allowed_tools(tmp_path, monkeypatch):
    fake = _fake_bin(tmp_path, "claude_args.sh", 'echo "$@"')
    monkeypatch.setenv("MARSHAL_CLAUDE_BIN", fake)
    monkeypatch.setenv("MARSHAL_CLAUDE_PERMISSION_MODE", "acceptEdits")
    monkeypatch.setenv("MARSHAL_CLAUDE_ALLOWED_TOOLS", "Bash Read Write")
    out = _invoke_claude("do the review", cwd=str(tmp_path), timeout_s=10)
    assert "--permission-mode acceptEdits" in out
    assert "--allowedTools Bash Read Write" in out


def test_invoke_claude_omits_perm_flags_when_unset(tmp_path, monkeypatch):
    fake = _fake_bin(tmp_path, "claude_args2.sh", 'echo "$@"')
    monkeypatch.setenv("MARSHAL_CLAUDE_BIN", fake)
    monkeypatch.delenv("MARSHAL_CLAUDE_PERMISSION_MODE", raising=False)
    monkeypatch.delenv("MARSHAL_CLAUDE_ALLOWED_TOOLS", raising=False)
    out = _invoke_claude("x", cwd=str(tmp_path), timeout_s=10)
    assert "--permission-mode" not in out
    assert "--allowedTools" not in out


def test_deep_prompt_is_scoped_and_budgeted(monkeypatch):
    from marshal_core.worker import _deep_prompt
    monkeypatch.setenv("MARSHAL_DEEP_BUDGET_MIN", "40")
    monkeypatch.setenv("MARSHAL_DEEP_MAX_FINDINGS", "8")
    p = _deep_prompt({"change_ref": "abc123", "repo": "node"})
    assert "abc123" in p and "node" in p
    assert "DIFF of this commit" in p and "NOT the whole repository" in p   # scoped
    assert "40 minutes" in p and "cap proven findings at 8" in p            # budgeted
    assert "NEVER run unbounded" in p                                        # converge, don't hang
    assert "do NOT post" in p and "MARSHAL_VERDICT.json" in p               # local-only + contract


def test_deep_prompt_pr_mode_reviews_full_pr(monkeypatch):
    from marshal_core.worker import _deep_prompt
    p = _deep_prompt({"change_ref": "abc123", "repo": "node"}, pr_number=1200)
    assert "/marshal deep node 1200" in p        # native PR-mode invocation
    assert "FULL PR diff" in p                    # full PR, not a single commit
    assert "do NOT post" in p and "MARSHAL_VERDICT.json" in p
    assert "NEVER run unbounded" in p             # budget still applies


def test_resolve_pr_number_prefers_head_match(monkeypatch):
    import marshal_core.github_backfill as gb
    from marshal_core.worker import _resolve_pr_number
    monkeypatch.setattr(gb, "fetch_pulls", lambda org, repo, sha: [
        {"number": 1257, "head": {"sha": "other"}},
        {"number": 1200, "head": {"sha": "abc123"}},   # commit is the HEAD of 1200
    ])
    assert _resolve_pr_number("node", "abc123") == 1200


def test_resolve_pr_number_none_when_not_a_pr_head(monkeypatch):
    import marshal_core.github_backfill as gb
    from marshal_core.worker import _resolve_pr_number
    monkeypatch.setattr(gb, "fetch_pulls", lambda org, repo, sha: [])
    assert _resolve_pr_number("node", "abc123") is None
