"""事件路由 / 编排 (本切片: PR → 不变量门禁 → 决策)。"""
from marshal_core.contracts import (
    NormalizedEvent, DispatchJob, StructuredResult, GateDecision, PlanResponse,
)
from marshal_core.domain_pack import DomainPack
from marshal_core.knowledge.store import Store
from marshal_core.modules.invariant_gate import InvariantGate, event_scope


class Orchestrator:
    def __init__(self, pack: DomainPack, store: Store):
        self.pack = pack
        self.store = store
        self.gate = InvariantGate(pack)

    def handle_event(self, event: NormalizedEvent) -> DispatchJob:
        for inv in self.pack.list_invariants(event_scope(event)):
            self.store.register_invariant(
                id=inv.id, domain_pack=self.pack.id, domain=inv.domain,
                spec_ref=inv.spec_ref, executor_kind=inv.executor_kind,
                location_repo=inv.location_repo, location_path=inv.location_path,
                location_test=inv.location_test, severity=inv.severity)
        job = self.gate.build_dispatch(event)
        self.store.audit(event="dispatch", refs={"job_id": job.job_id,
                                                 "change_ref": event.change_ref})
        return job

    def handle_result(self, event: NormalizedEvent, result: StructuredResult) -> GateDecision:
        job = self.gate.build_dispatch(event)
        decision = self.gate.evaluate(event, job, result)
        self.store.record_gate_run(change_ref=event.change_ref, job_id=job.job_id,
                                   verdict=decision.verdict,
                                   evidence={"gates": decision.gates})
        self.store.audit(event="decision", decision=decision.verdict,
                         refs={"change_ref": event.change_ref})
        return decision

    def plan(self, event: NormalizedEvent) -> PlanResponse:
        """告诉 CI 执行器: 本次改动跑哪些不变量、各自怎么跑。复用 handle_event 的登记。"""
        job = self.handle_event(event)
        invs = self.pack.list_invariants(event_scope(event))
        return PlanResponse(
            job_id=job.job_id,
            invariants=[{"invariant_id": i.id, "run_command": i.run_command}
                        for i in invs],
        )
