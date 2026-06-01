from marshal_pack_cowboy.pack import CowboyPack


def test_spec_layers_manifest():
    pack = CowboyPack()
    layers = {layer["id"]: layer for layer in pack.spec_layers()}
    assert layers["whitepaper"]["role"] == "constitution"
    assert layers["cip"]["role"] == "amendment"
    assert layers["whitepaper"]["repo"] == "cowboy"
    assert layers["cip"]["source"] == "docs/cips"
    assert layers["whitepaper"]["source"] == "docs/whitepaper"


def test_resolve_cip_ref_to_glob():
    pack = CowboyPack()
    r = pack.resolve_spec_ref("CIP-3")
    assert r is not None
    assert r["layer"] == "cip"
    assert r["repo"] == "cowboy"
    assert r["path_glob"] == "docs/cips/cip-3-*.md"


def test_resolve_non_cip_refs_return_none():
    pack = CowboyPack()
    assert pack.resolve_spec_ref("C-1") is None
    assert pack.resolve_spec_ref("M-B") is None
    assert pack.resolve_spec_ref("CIP-?") is None
    assert pack.resolve_spec_ref("") is None


def test_resolve_whitepaper_ref():
    pack = CowboyPack()
    r = pack.resolve_spec_ref("WP")
    assert r["layer"] == "whitepaper"
    assert r["repo"] == "cowboy"
    assert r["path_glob"] == "docs/whitepaper/cowboy-technical-whitepaper.md"
