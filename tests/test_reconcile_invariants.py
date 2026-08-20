"""Reconcile: seed catalog invariants the demand-driven DB registry is missing."""
from marshal_core.domain_pack import InvariantDef
from marshal_core.knowledge.store import Store
from marshal_pack_cowboy.pack import CowboyPack


def _def(id_, repo="cbqs", pending=False):
    return InvariantDef(id=id_, domain="d", spec_ref="CIP-39", executor_kind="test",
                        location_repo=repo, location_path="p", location_test="t",
                        severity="high", run_command=["true"], pending=pending)


def test_dry_run_reports_added_but_writes_nothing(db_session):
    store = Store(db_session)
    plan = store.reconcile_invariants([_def("x.one")], apply=False)
    assert plan["added"] == ["x.one"]
    assert store.invariant_rows() == []          # dry-run must not write


def test_apply_writes_missing_with_hand_origin(db_session):
    store = Store(db_session)
    store.reconcile_invariants([_def("x.one")], apply=True)
    rows = store.invariant_rows()
    assert [r["id"] for r in rows] == ["x.one"]
    # origin faithfulness: no escape spawned it -> hand
    from marshal_core.knowledge.models import InvariantRegistry
    got = db_session.get(InvariantRegistry, "x.one")
    assert got.origin == "hand" and got.escape_id is None


def test_apply_recovers_ratchet_origin_from_escape(db_session):
    store = Store(db_session)
    store.open_escape(id="esc-9", description="d", root_cause_class="c")
    store.close_escape("esc-9", spawned_check="x.ratcheted")
    # the check was recorded on the escape but not (yet) in the registry
    plan = store.reconcile_invariants([_def("x.ratcheted")], apply=True)
    assert plan["added"] == ["x.ratcheted"]
    from marshal_core.knowledge.models import InvariantRegistry
    got = db_session.get(InvariantRegistry, "x.ratcheted")
    assert got.origin == "ratchet" and got.escape_id == "esc-9"


def test_pending_defs_are_skipped(db_session):
    store = Store(db_session)
    plan = store.reconcile_invariants([_def("x.skeleton", pending=True)], apply=True)
    assert plan["pending"] == ["x.skeleton"]
    assert plan["added"] == []
    assert store.invariant_rows() == []          # phantom guard: never seed pending


def test_existing_rows_are_not_overwritten(db_session):
    store = Store(db_session)
    store.register_invariant(id="x.one", domain_pack="cowboy", domain="d",
                             spec_ref="", executor_kind="test", location_repo="cbqs",
                             location_path="p", location_test="t", severity="high",
                             origin="ratchet", escape_id="esc-orig")
    plan = store.reconcile_invariants([_def("x.one")], apply=True)
    assert plan["present"] == ["x.one"]
    from marshal_core.knowledge.models import InvariantRegistry
    got = db_session.get(InvariantRegistry, "x.one")
    assert got.origin == "ratchet" and got.escape_id == "esc-orig"   # preserved


def test_allow_ids_gate_holds_back_unverified(db_session):
    store = Store(db_session)
    plan = store.reconcile_invariants(
        [_def("x.green"), _def("x.red")], apply=True, allow_ids={"x.green"})
    assert plan["added"] == ["x.green"]
    assert plan["unverified"] == ["x.red"]
    assert [r["id"] for r in store.invariant_rows()] == ["x.green"]


def test_pack_all_invariant_defs_is_complete_and_deduped():
    defs = CowboyPack().all_invariant_defs()
    ids = [d.id for d in defs]
    assert len(ids) == len(set(ids))             # deduped
    repos = {d.location_repo for d in defs}
    # every onboarded cbX repo + node contribute to the full catalog
    assert {"node", "cbfs", "cbss", "cbqs"} <= repos
    assert "cbqs.at_least_once_safe_prefix_holds" in ids
