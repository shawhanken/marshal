from marshal_core.review import aggregate_review, verify_findings


def _f(file, line, dim, sev, source, title="x"):
    return {"file": file, "line": line, "dimension": dim, "severity": sev,
            "source": source, "title": title}


def test_two_lenses_agree_reaches_quorum():
    findings = [
        _f("a.rs", 10, "correctness", "mid", "lens-correctness"),
        _f("a.rs", 10, "correctness", "mid", "lens-security"),
    ]
    out = aggregate_review(findings, quorum=2)
    g = out["groups"][0]
    assert g["support"] == 2 and g["status"] == "confirmed"
    assert out["review_verdict"] == "pass"  # confirmed but not high


def test_high_severity_escalates_even_with_single_support():
    findings = [_f("b.rs", 5, "security", "high", "lens-security")]
    out = aggregate_review(findings, quorum=2)
    assert out["groups"][0]["status"] == "needs_human"
    assert out["review_verdict"] == "needs_human"
    assert len(out["needs_human"]) == 1


def test_lone_low_severity_is_dropped_as_noise():
    findings = [_f("c.rs", 7, "style", "low", "lens-correctness")]
    out = aggregate_review(findings, quorum=2)
    assert out["groups"][0]["status"] == "weak"
    assert out["dropped"] and not out["confirmed"]
    assert out["review_verdict"] == "pass"


def test_group_takes_max_severity_and_distinct_sources():
    findings = [
        _f("d.rs", 1, "econ", "mid", "lens-econ"),
        _f("d.rs", 1, "econ", "high", "lens-correctness"),
        _f("d.rs", 1, "econ", "low", "lens-econ"),  # duplicate source
    ]
    out = aggregate_review(findings, quorum=2)
    g = out["groups"][0]
    assert g["severity"] == "high"
    assert g["support"] == 2  # distinct sources only
    assert g["status"] == "needs_human"


def test_explicit_key_overrides_file_line_dimension():
    findings = [
        {"key": "K", "severity": "mid", "source": "a", "title": "t"},
        {"key": "K", "severity": "mid", "source": "b", "title": "t"},
    ]
    out = aggregate_review(findings, quorum=2)
    assert len(out["groups"]) == 1 and out["groups"][0]["support"] == 2


def _v(*refuted):
    return [{"refuted": r} for r in refuted]


def test_verify_survives_on_majority_uphold():
    items = [{"key": "k1", "severity": "mid", "votes": _v(False, False, True)}]
    out = verify_findings(items)
    assert [g["key"] for g in out["survived"]] == ["k1"]
    assert not out["killed"]


def test_verify_kills_on_majority_refute():
    items = [{"key": "k2", "severity": "mid", "votes": _v(True, True, False)}]
    out = verify_findings(items)
    assert [g["key"] for g in out["killed"]] == ["k2"]


def test_verify_tie_kills_default_to_refute():
    items = [{"key": "k3", "severity": "high", "votes": _v(True, False)}]
    out = verify_findings(items)
    assert [g["key"] for g in out["killed"]] == ["k3"]
    assert out["verdict"] == "pass"  # the lone high was killed


def test_verify_no_votes_is_unverified():
    items = [{"key": "k4", "severity": "low", "votes": []}]
    out = verify_findings(items)
    assert [g["key"] for g in out["unverified"]] == ["k4"]


def test_verify_surviving_high_needs_human():
    items = [{"key": "k5", "severity": "high", "votes": _v(False, False, False)}]
    out = verify_findings(items)
    assert out["survived"] and out["verdict"] == "needs_human"
