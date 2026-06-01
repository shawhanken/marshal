"""② 不变量门禁编排 (机制, 领域无关)。不变量内容来自领域包。"""
from marshal_core.contracts import NormalizedEvent, DispatchJob, StructuredResult, GateDecision
from marshal_core.domain_pack import DomainPack


class InvariantGate:
    def __init__(self, pack: DomainPack):
        self.pack = pack

    def build_dispatch(self, event: NormalizedEvent) -> DispatchJob:
        invs = self.pack.list_invariants({"repo": event.repo,
                                          "diff_paths": event.diff_paths})
        return DispatchJob(
            job_id=f"inv-{event.change_ref}",
            kind="invariant",
            target_repo=event.repo,
            change_ref=event.change_ref,
            params={"invariant_ids": [i.id for i in invs]},
        )

    def evaluate(self, event: NormalizedEvent, job: DispatchJob,
                 result: StructuredResult) -> GateDecision:
        tier = self.pack.classify({"repo": event.repo, "diff_paths": event.diff_paths})

        if result.status != "ok":
            verdict = "needs_human" if tier == "high" else "pass"
            return GateDecision(change_ref=event.change_ref, tier=tier,
                gates=[{"name": "invariants", "outcome": "degraded",
                        "evidence_ref": job.job_id}], verdict=verdict)

        results = result.payload.get("results", [])
        failed = [r for r in results if not r["passed"]]
        outcome = "fail" if failed else "pass"
        verdict = "block" if failed else "pass"
        return GateDecision(change_ref=event.change_ref, tier=tier,
            gates=[{"name": "invariants", "outcome": outcome,
                    "evidence_ref": job.job_id}], verdict=verdict)
