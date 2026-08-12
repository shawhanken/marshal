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


def _manifest(**overrides):
    manifest = {
        "head_sha": "head",
        "base_sha": "base",
        "tree_sha": "tree",
        "platform": "linux-x86_64",
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


def test_complete_review_run_requires_resolved_evidence(db_session):
    store = Store(db_session)
    run = store.open_review_run(change_ref="head")
    closed = store.close_review_run(run.id, "complete", _manifest())
    assert closed.status == "complete"
    assert closed.evidence["external_scans"][0]["findings"] == 0
    sparse = store.open_review_run(change_ref="sparse")
    with pytest.raises(ValueError):
        store.close_review_run(sparse.id, "complete", {"head_sha": "head"})


def test_unavailable_scan_is_not_zero_findings(db_session):
    store = Store(db_session)
    run = store.open_review_run(change_ref="head")
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
    run = store.open_review_run(change_ref="head")
    with pytest.raises(ValueError, match="unresolved"):
        store.close_review_run(run.id, "complete", _manifest(
            lenses={"expected": ["correctness", "replay"],
                    "returned": ["correctness"], "missing": ["replay"]}))
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
    run = store.open_review_run(change_ref="head")
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
    run = store.open_review_run(change_ref="head")
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
    run = store.open_review_run(change_ref="head")
    store.close_review_run(run.id, "complete", _manifest())
    with pytest.raises(ValueError, match="already closed"):
        store.close_review_run(run.id, "degraded", _manifest(
            external_scans=[{"name": "almanax", "status": "unavailable",
                             "reason": "quota reached"}]))
    with pytest.raises(ValueError, match="already closed"):
        store.record_finding(run_id=run.id, key="late")



def _cli(args, db):
    env = dict(os.environ, MARSHAL_DB=f"sqlite:///{db}",
               PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    return subprocess.run([sys.executable, "-m", "marshal_core.cli", *args],
                          capture_output=True, text=True, env=env,
                          cwd=str(Path(__file__).resolve().parents[1]))


def test_cli_review_run_close_and_show(tmp_path):
    db = tmp_path / "trace.db"
    opened = _cli(["review-run-open", "--change-ref", "head"], db)
    assert opened.returncode == 0, opened.stderr
    run_id = json.loads(opened.stdout)["run_id"]
    closed = _cli(["review-run-close", "--run-id", str(run_id), "--status", "complete",
                   "--evidence-json", json.dumps(_manifest())], db)
    assert closed.returncode == 0, closed.stderr
    shown = _cli(["review-run-show", "--run-id", str(run_id)], db)
    assert shown.returncode == 0, shown.stderr
    body = json.loads(shown.stdout)
    assert body["status"] == "complete"
    assert body["evidence"]["head_sha"] == "head"
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
