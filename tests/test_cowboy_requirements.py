from marshal_pack_cowboy.pack import CowboyPack


def test_parse_extracts_rfc2119_normative_clauses():
    pack = CowboyPack()
    text = (
        "Section 1\n"
        "The fee MUST be burned at settlement.\n"
        "This is ordinary prose with no obligation.\n"
        "Producers MUST NOT encode numeric tags as integers.\n"
        "Clients SHOULD retry on transient errors.\n"
        "An actor MAY cache the result.\n"
    )
    reqs = pack.parse_spec_requirements(text)
    pairs = {(r["keyword"], r["level"]) for r in reqs}
    assert ("MUST", "must") in pairs
    assert ("MUST NOT", "must") in pairs
    assert ("SHOULD", "should") in pairs
    assert ("MAY", "may") in pairs
    # non-normative prose excluded
    assert all("ordinary prose" not in r["text"] for r in reqs)
    # exactly two MUST-level obligations
    assert sum(1 for r in reqs if r["level"] == "must") == 2
    # stable sequential ids
    assert [r["id"] for r in reqs] == [f"req-{i}" for i in range(1, len(reqs) + 1)]


def test_must_not_takes_precedence_over_must_on_same_line():
    pack = CowboyPack()
    reqs = pack.parse_spec_requirements("A node MUST NOT do X.")
    assert len(reqs) == 1
    assert reqs[0]["keyword"] == "MUST NOT"


def test_lowercase_must_is_not_normative():
    pack = CowboyPack()
    # RFC2119 keywords are normative only in uppercase.
    assert pack.parse_spec_requirements("you must be kind and may relax") == []
