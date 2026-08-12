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

        def _degraded(reason: str) -> GateDecision:
            return GateDecision(change_ref=event.change_ref, tier=tier,
                gates=[{"name": "invariants", "outcome": "degraded",
                        "evidence_ref": job.job_id, "reason": reason}],
                verdict="escalate")

        if result.job_id != job.job_id:
            return _degraded(f"result job_id {result.job_id!r} does not match {job.job_id!r}")
        if result.kind != job.kind:
            return _degraded(f"result kind {result.kind!r} does not match {job.kind!r}")

        payload = result.payload
        if not isinstance(payload, dict):
            return _degraded("result payload must be an object")
        results = payload.get("results")
        if not isinstance(results, list):
            return _degraded("result payload.results must be a list")
        not_run = payload.get("not_run", [])
        if not isinstance(not_run, list):
            return _degraded("result payload.not_run must be a list")

        for index, row in enumerate(results):
            if not isinstance(row, dict):
                return _degraded(f"result row {index} must be an object")
            if not isinstance(row.get("invariant_id"), str) or not row["invariant_id"]:
                return _degraded(f"result row {index} has an invalid invariant_id")
            if type(row.get("passed")) is not bool:
                return _degraded(
                    f"result row {index}.passed must be a JSON boolean")

        # An explicit, well-typed failure remains a hard block even when another
        # check was not runnable. Missing coverage is escalated only when there
        # is no concrete failure to block on.
        failed = [row for row in results if row["passed"] is False]
        if failed:
            return GateDecision(change_ref=event.change_ref, tier=tier,
                gates=[{"name": "invariants", "outcome": "fail",
                        "evidence_ref": job.job_id}], verdict="block")
        if result.status != "ok":
            return _degraded(f"executor reported status {result.status!r}")
        if not_run:
            return _degraded("executor reported not_run entries with status 'ok'")

        expected = job.params.get("invariant_ids", [])
        if not isinstance(expected, list) or any(not isinstance(i, str) or not i
                                                 for i in expected):
            return _degraded("dispatch plan contains invalid invariant ids")
        returned = [row["invariant_id"] for row in results]
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
