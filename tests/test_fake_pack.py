"""Fake-pack contract test: core is domain-agnostic.

Demonstrates core marshalling logic works with any DomainPack, not just Cowboy.
This is a regression test to ensure core doesn't depend on cowboy-specific knowledge.
"""
from marshal_core.contracts import NormalizedEvent, StructuredResult
from marshal_core.domain_pack import InvariantDef
from marshal_core.knowledge.store import Store
from marshal_core.modules.orchestrator import Orchestrator


class FakePack:
    """Minimal domain pack unrelated to Cowboy."""

    @property
    def id(self) -> str:
        return "fake"

    def list_invariants(self, scope: dict) -> list[InvariantDef]:
        return [
            InvariantDef(
                id="fake.always",
                domain="generic",
                spec_ref="RFC-1",
                executor_kind="proptest",
                location_repo="anyrepo",
                location_path="x",
                location_test="t",
                severity="low",
            )
        ]

    def classify(self, scope: dict) -> str:
        return "low"


def test_core_runs_with_arbitrary_pack(db_session):
    """Core orchestrator should work with any pack, not just Cowboy."""
    orch = Orchestrator(pack=FakePack(), store=Store(db_session))
    ev = NormalizedEvent(kind="pr", repo="anyrepo", change_ref="z9")
    job = orch.handle_event(ev)
    assert job.params["invariant_ids"] == ["fake.always"]
    res = StructuredResult(
        job_id=job.job_id,
        kind="invariant",
        status="ok",
        payload={
            "results": [
                {"invariant_id": "fake.always", "passed": True, "detail": ""}
            ]
        },
    )
    assert orch.handle_result(ev, res).verdict == "pass"
