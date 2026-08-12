import warnings
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from marshal_core.knowledge.models import Base, ensure_schema
from marshal_core.knowledge.store import Store
from marshal_core.knowledge.evidence import evidence_has_unresolved, validate_review_evidence


HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
TREE_SHA = "c" * 40
REVIEW_PLAN = {"expected_lenses": ["correctness"],
               "expected_commands": ["invariants"],
               "expected_external_scans": ["almanax"]}

def _manifest(**overrides):
    manifest = {
        "head_sha": HEAD_SHA,
        "base_sha": BASE_SHA,
        "tree_sha": TREE_SHA,
        "platform": "linux-x86_64",
        "worktree": "/tmp/review-worktree",
        "toolchain": "python3.12",
        "context_ref": "marshal@abc closure:7-files",
        "steps": {
            "closure": {"status": "complete"},
            "scout": {"status": "complete"},
            "prove": {"status": "complete"},
            "invariant": {"status": "complete"},
        },
        "lenses": {"expected": ["correctness"], "returned": ["correctness"], "missing": []},
        "commands": [{
            "name": "invariants", "status": "pass", "argv": ["cargo", "test"],
            "exit_code": 0, "tests": {"passed": 8, "failed": 0, "skipped": 0},
            "log_ref": "sha256:log",
        }],
        "external_scans": [{"name": "almanax", "status": "complete", "findings": 0}],
    }
    manifest.update(overrides)
    return manifest

def _open(store, change_ref=HEAD_SHA):
    return store.open_review_run(change_ref=change_ref, **REVIEW_PLAN)



def test_complete_review_run_requires_resolved_evidence(db_session):
    store = Store(db_session)
    run = _open(store)
    closed = store.close_review_run(run.id, "complete", _manifest())
    assert closed.status == "complete"
    assert closed.evidence["external_scans"][0]["findings"] == 0
    sparse = _open(store, "sparse")
    with pytest.raises(ValueError):
        store.close_review_run(sparse.id, "complete", {"head_sha": "head"})


def test_unavailable_scan_is_not_zero_findings(db_session):
    store = Store(db_session)
    run = _open(store)
    unavailable = _manifest(external_scans=[{
        "name": "almanax", "status": "unavailable", "findings": 0,
        "reason": "quota reached",
    }])
    with pytest.raises(ValueError, match="not zero findings"):
        store.close_review_run(run.id, "degraded", unavailable)
    degraded = _manifest(external_scans=[{
        "name": "almanax", "status": "unavailable", "reason": "quota reached",
    }])
    assert store.close_review_run(run.id, "degraded", degraded).status == "degraded"


def test_complete_review_run_rejects_missing_lens_or_failed_command(db_session):
    store = Store(db_session)
    run = _open(store)
    with pytest.raises(ValueError, match="unresolved"):
        store.close_review_run(run.id, "complete", _manifest(
            lenses={"expected": ["correctness"],
                    "returned": [], "missing": ["correctness"]}))
    with pytest.raises(ValueError):
        store.close_review_run(run.id, "complete", _manifest(
            commands=[{"name": "invariants", "status": "not_run"}]))


def test_evidence_validation_and_unresolved_helper():
    assert not evidence_has_unresolved(validate_review_evidence(_manifest()))
    assert evidence_has_unresolved(validate_review_evidence(_manifest(
        steps={"scout": {"status": "degraded", "reason": "agent stalled"}})))
    assert evidence_has_unresolved(validate_review_evidence(_manifest(
        lenses={"expected": ["correctness", "replay"],
                "returned": ["correctness"], "missing": []})))
    with pytest.raises(ValueError, match="both returned and missing"):
        validate_review_evidence(_manifest(
            lenses={"expected": ["correctness"], "returned": ["correctness"],
                    "missing": ["correctness"]}))
    with pytest.raises(ValueError, match="JSON object"):
        validate_review_evidence([])


def test_complete_manifest_rejects_empty_or_arbitrary_sections(db_session):
    store = Store(db_session)
    run = _open(store)
    empty = {
        "head_sha": "head", "base_sha": "base", "tree_sha": "tree",
        "steps": {}, "lenses": {}, "commands": [], "external_scans": [],
    }
    with pytest.raises(ValueError):
        store.close_review_run(run.id, "complete", empty)
    arbitrary = _manifest(steps={"junk": {"status": "complete"}})
    with pytest.raises(ValueError):
        store.close_review_run(run.id, "complete", arbitrary)


def test_complete_manifest_binds_sha_and_command_results(db_session):
    store = Store(db_session)
    run = _open(store)
    with pytest.raises(ValueError, match="head_sha"):
        store.close_review_run(run.id, "complete", _manifest(head_sha="other"))
    with pytest.raises(ValueError, match="non-zero exit_code"):
        store.close_review_run(run.id, "complete", _manifest(
            commands=[{"name": "invariants", "status": "pass", "argv": ["false"],
                       "exit_code": 1, "log_ref": "sha256:log"}]))
    with pytest.raises(ValueError, match="failed tests"):
        store.close_review_run(run.id, "complete", _manifest(
            commands=[{"name": "invariants", "status": "pass", "argv": ["test"],
                       "exit_code": 0, "tests": {"failed": 1},
                       "log_ref": "sha256:log"}]))


def test_closed_review_run_is_immutable(db_session):
    store = Store(db_session)
    run = _open(store)
    finding = store.record_finding(run_id=run.id, key="closed")
    store.close_review_run(run.id, "complete", _manifest())
    with pytest.raises(ValueError, match="already closed"):
        store.close_review_run(run.id, "degraded", _manifest(
            external_scans=[{"name": "almanax", "status": "unavailable",
                             "reason": "quota reached"}]))
    with pytest.raises(ValueError, match="already closed"):
        store.record_finding(run_id=run.id, key="late")
    with pytest.raises(ValueError, match="already closed"):
        store.set_human_verdict(finding.id, "accepted")



def _cli(args, db):
    env = dict(os.environ, MARSHAL_DB=f"sqlite:///{db}",
               PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    return subprocess.run([sys.executable, "-m", "marshal_core.cli", *args],
                          capture_output=True, text=True, env=env,
                          cwd=str(Path(__file__).resolve().parents[1]))


def test_cli_review_run_close_and_show(tmp_path):
    db = tmp_path / "trace.db"
    opened = _cli([
        "review-run-open", "--change-ref", HEAD_SHA,
        "--expected-lenses-json", json.dumps(REVIEW_PLAN["expected_lenses"]),
        "--expected-commands-json", json.dumps(REVIEW_PLAN["expected_commands"]),
        "--expected-external-scans-json",
        json.dumps(REVIEW_PLAN["expected_external_scans"]),
    ], db)
    assert opened.returncode == 0, opened.stderr
    run_id = json.loads(opened.stdout)["run_id"]
    closed = _cli(["review-run-close", "--run-id", str(run_id), "--status", "complete",
                   "--evidence-json", json.dumps(_manifest())], db)
    assert closed.returncode == 0, closed.stderr
    shown = _cli(["review-run-show", "--run-id", str(run_id)], db)
    assert shown.returncode == 0, shown.stderr
    body = json.loads(shown.stdout)
    assert body["status"] == "complete"
    assert body["evidence"]["head_sha"] == HEAD_SHA
    assert body["findings"] == []


def test_existing_review_schema_is_migrated(tmp_path):
    db = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX ix_review_run_status"))
        conn.execute(text("ALTER TABLE review_run DROP COLUMN status"))
        conn.execute(text("ALTER TABLE review_run DROP COLUMN evidence"))

    ensure_schema(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("review_run")}
    assert {"status", "evidence"} <= columns
    finding_indexes = {index["name"]: index for index in inspect(engine).get_indexes("review_finding")}
    assert finding_indexes["uq_review_finding_run_key"]["unique"] == 1

def test_complete_manifest_rejects_unproven_provenance_and_plan_identity(db_session):
    store = Store(db_session)
    run = _open(store)
    with pytest.raises(ValueError, match="exit_code"):
        store.close_review_run(run.id, "complete", _manifest(
            commands=[{
                "name": "invariants", "status": "pass", "argv": ["true"],
                "exit_code": None, "log_ref": "sha256:log",
            }]))
    with pytest.raises(ValueError, match="hexadecimal SHA"):
        store.close_review_run(run.id, "complete", _manifest(base_sha="not-a-sha"))
    with pytest.raises(ValueError, match="does not match"):
        store.close_review_run(run.id, "complete", _manifest(
            lenses={"expected": ["bogus"], "returned": ["bogus"], "missing": []}))
    with pytest.raises(ValueError, match="does not match"):
        store.close_review_run(run.id, "complete", _manifest(
            external_scans=[{"name": "made-up", "status": "complete", "findings": 0}]))


def test_degraded_manifest_requires_named_steps_and_command_reasons(db_session):
    store = Store(db_session)
    run = _open(store)
    with pytest.raises(ValueError, match="exactly"):
        store.close_review_run(run.id, "degraded", _manifest(
            steps={"junk": {"status": "degraded", "reason": "missing"}}))
    with pytest.raises(ValueError, match="argv"):
        store.close_review_run(run.id, "degraded", _manifest(
            commands=[{"name": "invariants", "status": "pass"}]))

def test_duplicate_legacy_findings_defer_unique_index_without_data_loss():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE review_finding (id INTEGER PRIMARY KEY, run_id INTEGER, "
            "key VARCHAR, title VARCHAR, claim VARCHAR, location VARCHAR, severity VARCHAR, "
            "lens VARCHAR, votes JSON, quorum_verdict VARCHAR, human_verdict VARCHAR, "
            "human_note VARCHAR)"
        ))
        conn.execute(text(
            "INSERT INTO review_finding (run_id, key) VALUES (1, 'duplicate'), (1, 'duplicate')"
        ))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ensure_schema(engine)
    assert any("unique index deferred" in str(item.message) for item in caught)
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT COUNT(*) FROM review_finding WHERE run_id = 1 AND key = 'duplicate'"
        )).scalar_one() == 2
