"""⑧ review trace: 一次 review 的 provenance + finding 级裁决链落库。"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marshal_core.knowledge.store import Store


def test_open_run_and_record_findings(db_session):
    store = Store(db_session)
    run = store.open_review_run(change_ref="abc", repo="node", mode="deep",
                                host="claude", model="claude-opus-5",
                                skill_rev="1846036",
                                context_ref="node@abc closure:7-files")
    assert run.id
    store.record_finding(run_id=run.id, key="fee-drift", title="fee drift",
                         claim="if fee split changes then conservation breaks",
                         location="execution/src/fees.rs::split",
                         severity="high", lens="econ",
                         votes=[{"lens": "reachability", "refuted": False}],
                         quorum_verdict="survived")
    got = store.list_findings(run.id)
    assert [f.key for f in got] == ["fee-drift"]
    assert got[0].human_verdict == ""          # 未终审 → 空, 不预填


def test_human_verdict_updates_finding(db_session):
    store = Store(db_session)
    run = store.open_review_run(change_ref="abc")
    f = store.record_finding(run_id=run.id, key="k1", quorum_verdict="survived")
    updated = store.set_human_verdict(f.id, "accepted", note="fixed in abc123")
    assert updated.human_verdict == "accepted"
    assert updated.human_note == "fixed in abc123"


def test_human_verdict_rejects_bad_input(db_session):
    store = Store(db_session)
    run = store.open_review_run(change_ref="abc")
    f = store.record_finding(run_id=run.id, key="k1")
    with pytest.raises(ValueError):
        store.set_human_verdict(f.id, "maybe")
    with pytest.raises(ValueError):
        store.set_human_verdict(99999, "accepted")


def test_escape_links_fix_ref_and_missing_run(db_session):
    store = Store(db_session)
    run = store.open_review_run(change_ref="abc")
    esc = store.open_escape(id="esc-1", description="d", root_cause_class="rc",
                            missed_by_run_id=run.id)
    assert esc.missed_by_run_id == run.id
    closed = store.close_escape("esc-1", spawned_check="inv.x", fix_ref="fix789")
    assert closed.fix_ref == "fix789"


def _cli(args, db):
    env = dict(os.environ, MARSHAL_DB=f"sqlite:///{db}")
    return subprocess.run([sys.executable, "-m", "marshal_core.cli", *args],
                          capture_output=True, text=True, env=env,
                          cwd=str(Path(__file__).resolve().parents[1]))


def test_cli_review_trace_roundtrip(tmp_path):
    db = tmp_path / "trace.db"

    r1 = _cli(["review-run-open", "--change-ref", "abc", "--repo", "node",
               "--mode", "deep", "--host", "claude", "--model", "m1"], db)
    assert r1.returncode == 0, r1.stderr
    run_id = json.loads(r1.stdout)["run_id"]

    votes = [{"key": "k1", "severity": "high",
              "votes": [{"refuted": False, "lens": "reachability"},
                        {"refuted": False, "lens": "severity"},
                        {"refuted": True, "lens": "stale-basis"}]},
             {"key": "k2", "severity": "low", "votes": []}]
    findings = [{"key": "k1", "title": "t1", "claim": "c1",
                 "location": "f.rs::x", "lens": "econ"}]
    r2 = _cli(["review-verify", "--votes-json", json.dumps(votes),
               "--run-id", str(run_id),
               "--findings-json", json.dumps(findings)], db)
    assert r2.returncode == 0, r2.stderr
    assert json.loads(r2.stdout)["survived"][0]["key"] == "k1"

    from marshal_core.knowledge.models import ReviewFinding
    s = sessionmaker(bind=create_engine(f"sqlite:///{db}"))()
    rows = {f.key: f for f in s.query(ReviewFinding).all()}
    assert rows["k1"].quorum_verdict == "survived"
    assert rows["k1"].title == "t1"            # findings-json detail joined by key
    assert rows["k2"].quorum_verdict == "unverified"
    fid = rows["k1"].id
    s.close()

    r3 = _cli(["finding-verdict", "--finding-id", str(fid),
               "--verdict", "accepted", "--note", "fixed"], db)
    assert r3.returncode == 0, r3.stderr
    s = sessionmaker(bind=create_engine(f"sqlite:///{db}"))()
    assert s.get(ReviewFinding, fid).human_verdict == "accepted"
    s.close()


def test_cli_review_verify_without_run_id_stays_pure(tmp_path):
    db = tmp_path / "pure.db"
    votes = [{"key": "k1", "severity": "low", "votes": [{"refuted": True}]}]
    r = _cli(["review-verify", "--votes-json", json.dumps(votes)], db)
    assert r.returncode == 0, r.stderr
    assert not db.exists()                     # 不带 --run-id 不落库, 行为不变