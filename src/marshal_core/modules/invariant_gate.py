"""② 不变量门禁编排 (机制, 领域无关)。不变量内容来自领域包。"""
from marshal_core.contracts import NormalizedEvent, DispatchJob, StructuredResult, GateDecision
from marshal_core.domain_pack import DomainPack


def event_scope(event: NormalizedEvent) -> dict:
    return {"repo": event.repo, "diff_paths": event.diff_paths,
            "labels": event.labels}


class InvariantGate:
    def __init__(self, pack: DomainPack):
        self.pack = pack

    def build_dispatch(self, event: NormalizedEvent) -> DispatchJob:
        invs = self.pack.list_invariants(event_scope(event))
        return DispatchJob(
            job_id=f"inv-{event.change_ref}",
            kind="invariant",
            target_repo=event.repo,
            change_ref=event.change_ref,
            params={"invariant_ids": [i.id for i in invs]},
        )

    def evaluate(self, event: NormalizedEvent, job: DispatchJob,
                 result: StructuredResult) -> GateDecision:
        tier = self.pack.classify(event_scope(event))

        def _degraded(reason: str) -> GateDecision:
            return GateDecision(change_ref=event.change_ref, tier=tier,
                gates=[{"name": "invariants", "outcome": "degraded",
                        "evidence_ref": job.job_id, "reason": reason}],
                verdict="escalate")

        if result.job_id != job.job_id:
            return _degraded(f"result job_id {result.job_id!r} does not match {job.job_id!r}")
        if result.kind != job.kind:
            return _degraded(f"result kind {result.kind!r} does not match {job.kind!r}")
        if result.status != "ok":
            return _degraded(f"executor reported status {result.status!r}")

        results = result.payload.get("results", [])
        failed = [r for r in results if not r["passed"]]
        if failed:
            return GateDecision(change_ref=event.change_ref, tier=tier,
                gates=[{"name": "invariants", "outcome": "fail",
                        "evidence_ref": job.job_id}], verdict="block")

        expected = job.params.get("invariant_ids", [])
        returned = [r["invariant_id"] for r in results]
        missing = sorted(set(expected) - set(returned))
        unknown = sorted(set(returned) - set(expected))
        duplicated = len(returned) != len(set(returned))
        if missing or unknown or duplicated:
            return _degraded(
                f"result set does not match plan: missing={missing} "
                f"unknown={unknown} duplicated={duplicated}")

        return GateDecision(change_ref=event.change_ref, tier=tier,
            gates=[{"name": "invariants", "outcome": "pass",
                    "evidence_ref": job.job_id}], verdict="pass")
