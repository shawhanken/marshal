import json
import os
import subprocess
import sys

from marshal_core.review import (
    NON_TRIGGER_DIMENSIONS,
    QUALITY_LENSES,
    adaptive_closure_budget,
    adaptive_pr_cap,
    aggregate_review,
    assign_refute_lenses,
    resolve_changed_anchor,
    select_pr_findings,
    verify_findings,
)


def _f(file, line, dim, sev, source, title="x", **extra):
    return {
        "file": file, "line": line, "dimension": dim,
        "severity": sev, "source": source, "title": title, **extra,
    }


def test_orthogonal_dimensions_within_10_lines_do_not_swallow_each_other():
    findings = [
        _f("src/networking.c", 2773, "maintainability", "mid", "lens-maint", "dead branch left after refactor"),
        _f("src/networking.c", 2776, "correctness", "high", "lens-correctness", "snprintf comma operator bug"),
        _f("src/networking.c", 2779, "performance", "mid", "lens-perf", "redundant strlen inside loop"),
    ]
    out = aggregate_review(findings, quorum=2, proximity=10)
    assert len(out["groups"]) == 3


def test_same_source_distinct_lines_split_into_separate_bugs_while_cross_lens_merges():
    findings = [
        _f("src/networking.c", 2773, "correctness", "high", "lens-correctness", "redaction order bug"),
        _f("src/networking.c", 2774, "security", "high", "lens-security", "auth password leak before redact"),
        _f("src/networking.c", 2776, "correctness", "high", "lens-correctness", "snprintf comma operator bug"),
    ]
    out = aggregate_review(findings, quorum=2, proximity=10)
    assert len(out["groups"]) == 2
    for g in out["groups"]:
        lo, hi = g["key"].rsplit(":", 1)[-1].split("~")
        assert int(hi) - int(lo) <= 10


def test_quality_lenses_are_domain_agnostic_and_trigger_exempt_in_refute():
    blob = " ".join(x["prompt"] for x in QUALITY_LENSES).lower()
    for forbidden in ("cip", "pvm", "gas", "receipt", "escrow", "cowboy"):
        assert forbidden not in blob
    for dim in NON_TRIGGER_DIMENSIONS:
        lenses = assign_refute_lenses(3, dimension=dim)
        assert all(x["name"] not in ("reachability", "severity") for x in lenses)
    items = [{
        "key": "~prox:maintainability:a.py:10~10",
        "dimension": "maintainability",
        "severity": "mid",
        "votes": [
            {"lens": "reachability", "refuted": True},
            {"lens": "severity", "refuted": True},
            {"lens": "stale-basis", "refuted": False},
            {"lens": "intended-design", "refuted": False},
            {"lens": "already-guarded", "refuted": True},
        ],
    }]
    verified = verify_findings(items)
    assert len(verified["survived"]) == 1


def test_select_pr_findings_retains_confirmed_test_defects_and_anchors_headers():
    changed = ["src/service.cpp", "tests/test_service.cpp"]
    candidates = [
        {"file": "include/service.h", "line": 22, "dimension": "correctness",
         "severity": "high", "status": "escalate", "title": "ABI struct field mismatch"},
        {"file": "tests/test_service.cpp", "line": 45, "dimension": "correctness",
         "severity": "high", "status": "confirmed", "support": 2,
         "title": "assertion compares variable to itself (tautology)"},
        {"file": "tests/test_service.cpp", "line": 60, "dimension": "maintainability",
         "severity": "low", "status": "advisory",
         "title": "minor style nitpick in helper naming"},
    ]
    selected = select_pr_findings(candidates, changed_files=changed, changed_lines=240)
    assert len(selected) == 2
    assert {x["file"] for x in selected} == {"src/service.cpp", "tests/test_service.cpp"}


def test_quorum_groups_pipe_directly_into_select_pr_findings_and_cli():
    findings = [
        _f("src/net.c", 100, "correctness", "high", "lens-correctness", "use-after-free on close"),
        _f("src/net.c", 104, "maintainability", "mid", "lens-maint", "unused fallback branch"),
    ]
    agg = aggregate_review(findings, quorum=2, proximity=10)
    selected = select_pr_findings(agg["groups"], changed_files=["src/net.c"], changed_lines=600)
    assert len(selected) == 2
    assert adaptive_pr_cap(changed_lines=950, changed_files=[f"src/m{i}.py" for i in range(12)]) >= 20
    assert adaptive_closure_budget(changed_lines=1200, file_count=14)["closure_lines"] > 3000
    assert resolve_changed_anchor("include/engine/executor.h", {"src/engine/executor.cpp"}) == "src/engine/executor.cpp"

    repo_root = os.path.dirname(os.path.dirname(__file__))
    env = {**os.environ, "PYTHONPATH": os.path.join(repo_root, "src")}
    p = subprocess.run(
        [sys.executable, "-m", "marshal_core.cli", "review-select",
         "--findings-json", json.dumps(agg["groups"]),
         "--changed-files", "src/net.c", "--changed-lines", "500"],
        capture_output=True, text=True, env=env, check=True,
    )
    assert json.loads(p.stdout)["count"] == 2
