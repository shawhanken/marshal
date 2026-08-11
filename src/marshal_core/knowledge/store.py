"""知识核读写薄封装。"""
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session
from .models import (
    InvariantRegistry, GateRun, AuditLog, EscapeRegistry, Concept, ConceptEdge,
    ConceptAnchorRow, ReviewRun, ReviewFinding,
)

_HUMAN_VERDICTS = {"accepted", "rejected", "modified"}


class Store:
    def __init__(self, session: Session):
        self.s = session

    def register_invariant(self, **kw) -> InvariantRegistry:
        inv = InvariantRegistry(**kw)
        self.s.merge(inv)
        self.s.commit()
        return inv

    def list_invariants(self, domain_pack: str, repo: str) -> list[InvariantRegistry]:
        stmt = select(InvariantRegistry).where(
            InvariantRegistry.domain_pack == domain_pack,
            InvariantRegistry.location_repo == repo,
            InvariantRegistry.status == "active",
        )
        return list(self.s.scalars(stmt))

    def record_gate_run(self, change_ref: str, job_id: str, verdict: str,
                        evidence: dict) -> GateRun:
        run = GateRun(change_ref=change_ref, job_id=job_id, verdict=verdict,
                      evidence=evidence)
        self.s.add(run)
        self.s.commit()
        return run

    def get_gate_run(self, run_id: int) -> GateRun | None:
        return self.s.get(GateRun, run_id)

    def audit(self, event: str, actor: str = "system", decision: str = "",
              refs: dict | None = None) -> None:
        self.s.add(AuditLog(event=event, actor=actor, decision=decision,
                            refs=refs or {}))
        self.s.commit()

    def open_escape(self, **kw) -> EscapeRegistry:
        esc = EscapeRegistry(**kw)
        self.s.add(esc)
        self.s.commit()
        return esc

    def get_escape(self, escape_id: str) -> EscapeRegistry | None:
        return self.s.get(EscapeRegistry, escape_id)

    def list_open_escapes(self) -> list[EscapeRegistry]:
        stmt = select(EscapeRegistry).where(EscapeRegistry.status == "open")
        return list(self.s.scalars(stmt))

    def list_escapes(self, domain_pack: str | None = None) -> list[EscapeRegistry]:
        """全部逃逸 (③ ratchet-lenses 的彈藥源); 可按 domain_pack 过滤。"""
        stmt = select(EscapeRegistry)
        if domain_pack is not None:   # '' 是"筛空名 pack", 非"不筛" (后者传 None)
            stmt = stmt.where(EscapeRegistry.domain_pack == domain_pack)
        return list(self.s.scalars(stmt))

    def metrics(self) -> dict:
        """⑦ 度量: 从知识核聚合方法论指标。诚实标注当前数据模型不支持的指标
        (escape_rate 缺总-bug 分母;time_to_detection 缺 introduced_at 时间戳;
        tiered_review_coverage 缺 Classifications 表),给 null + reason,不瞎编。
        """
        def _count(model, *where):
            stmt = select(func.count()).select_from(model)
            for w in where:
                stmt = stmt.where(w)
            return self.s.scalar(stmt)

        inv_active = _count(InvariantRegistry, InvariantRegistry.status == "active")
        inv_ratchet = _count(InvariantRegistry, InvariantRegistry.status == "active",
                             InvariantRegistry.origin == "ratchet")
        esc_open = _count(EscapeRegistry, EscapeRegistry.status == "open")
        esc_closed = _count(EscapeRegistry, EscapeRegistry.status == "closed")
        gate_total = _count(GateRun)
        gate_by_verdict = {
            v: _count(GateRun, GateRun.verdict == v)
            for v in ("pass", "block", "escalate")
        }
        return {
            "invariant_gate_count": inv_active,
            "ratchet_invariants": inv_ratchet,
            "escapes_open": esc_open,
            "escapes_closed": esc_closed,
            "ratchet_increment": esc_closed,   # 每个 closed escape 至少织出一条检查
            "gate_runs_total": gate_total,
            "gate_runs_by_verdict": gate_by_verdict,
            "unavailable": {
                "escape_rate": "needs a total-bug denominator (not tracked)",
                "mean_time_to_detection": "needs introduced_at as a timestamp (currently free string)",
                "tiered_review_coverage": "needs a Classifications table (not modeled in this slice)",
                "cip_conformance_pct": "use `conformance --spec-root <cowboy>`",
            },
        }

    def close_escape(self, escape_id: str, spawned_check: str,
                     fix_ref: str | None = None) -> EscapeRegistry:
        if not spawned_check:
            raise ValueError("cannot close escape without a spawned_check (棘轮纪律)")
        esc = self.s.get(EscapeRegistry, escape_id)
        if esc is None:
            raise ValueError(f"escape not found: {escape_id}")
        esc.spawned_check = spawned_check
        esc.status = "closed"
        if fix_ref is not None:
            esc.fix_ref = fix_ref
        self.s.commit()
        return esc

    def open_review_run(self, **kw) -> ReviewRun:
        run = ReviewRun(**kw)
        self.s.add(run)
        self.s.commit()
        return run

    def record_finding(self, **kw) -> ReviewFinding:
        f = ReviewFinding(**kw)
        self.s.add(f)
        self.s.commit()
        return f

    def list_findings(self, run_id: int) -> list[ReviewFinding]:
        stmt = select(ReviewFinding).where(ReviewFinding.run_id == run_id)
        return list(self.s.scalars(stmt))

    def set_human_verdict(self, finding_id: int, verdict: str,
                          note: str = "") -> ReviewFinding:
        if verdict not in _HUMAN_VERDICTS:
            raise ValueError(
                f"human verdict must be one of {sorted(_HUMAN_VERDICTS)}, got {verdict!r}")
        f = self.s.get(ReviewFinding, finding_id)
        if f is None:
            raise ValueError(f"finding not found: {finding_id}")
        f.human_verdict = verdict
        f.human_note = note
        self.s.commit()
        return f

    def upsert_concept(self, **kw) -> Concept:
        """派生写入 (单向 markdown→DB): 幂等 upsert 一个概念缓存行。"""
        c = Concept(**kw)
        self.s.merge(c)
        self.s.commit()
        return c

    def list_concepts(self, domain_pack: str) -> list[Concept]:
        stmt = select(Concept).where(Concept.domain_pack == domain_pack)
        return list(self.s.scalars(stmt))

    def concept_tree(self, domain_pack: str) -> list[dict]:
        """按 parent_id 组装 primary-parent 树 (可 review 骨架)。返回根列表, 每节点
        含 id/importance/confidence/doc_only/children。孤儿 (parent 不存在) 挂到根;
        parent 环成员 (self-parent / 互指) 亦挂到根暴露, 绝不静默丢弃 (人审 typo 常见),
        且不构造循环子引用 (否则 flatten / json.dumps 会无限循环)。"""
        concepts = self.list_concepts(domain_pack)
        nodes = {c.id: {"id": c.id, "importance": c.importance,
                        "confidence": c.confidence, "doc_only": c.doc_only,
                        "children": []}
                 for c in concepts}
        parent_of = {c.id: c.parent_id for c in concepts}

        def _reaches_cycle(start: str) -> bool:
            # 顺 parent 链上溯; 重访即环 (含 self-parent 与祖先环)。缺失的 parent 视为终点。
            seen: set[str] = set()
            cur = start
            while cur and cur in nodes:
                if cur in seen:
                    return True
                seen.add(cur)
                cur = parent_of.get(cur, "")
            return False

        roots = []
        for c in concepts:
            node = nodes[c.id]
            # 只在 parent 存在且此链无环时嵌套; 否则 (孤儿 / 环成员 / 环下后代) 挂到根暴露。
            parent = nodes.get(c.parent_id) if c.parent_id else None
            if parent is not None and not _reaches_cycle(c.id):
                parent["children"].append(node)
            else:
                roots.append(node)
        return roots

    def list_edges(self, concept_ids: set[str]) -> list[ConceptEdge]:
        """返回**任一端**落在 concept_ids 内的边。深审 run 结论:必须包含悬空引用
        (src 在但 dst 是未建概念)—— 否则 report 既漏"悬空引用"信号, 又把"只依赖了尚未
        建立的概念"的节点误报成 orphan(它的唯一边被过滤掉了)。"""
        stmt = select(ConceptEdge).where(
            or_(ConceptEdge.src_id.in_(concept_ids), ConceptEdge.dst_id.in_(concept_ids))
        )
        return list(self.s.scalars(stmt))

    def list_anchors(self, concept_ids: set[str]) -> list[ConceptAnchorRow]:
        """返回 concept_id 落在 concept_ids 内的全部锚点行 (verified 与否都含);
        空集合返回空列表。用于把概念映射回其代码/文档落点 (repo/path/symbol)。"""
        stmt = select(ConceptAnchorRow).where(ConceptAnchorRow.concept_id.in_(concept_ids))
        return list(self.s.scalars(stmt))
