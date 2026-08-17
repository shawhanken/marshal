"""知识核读写薄封装。"""
import json
import re
from datetime import timedelta

from sqlalchemy import select, func, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .evidence import (
    REQUIRED_STEPS, REVIEW_RUN_STATUSES, evidence_has_unresolved,
    validate_review_evidence,
)
from .models import (
    InvariantRegistry, GateRun, AuditLog, EscapeRegistry, Meta, ReviewJob, _now,
    Concept, ConceptEdge, ConceptAnchorRow, PlannedEvent, ReviewRun, ReviewFinding,
)

# The "awaiting human review" queue. `escalate` is the renamed-forward verdict for
# what older gate_runs recorded as `needs_human`; the two are the same bucket, so every
# inbox/metrics/timeseries read must treat them together (older DBs have only
# needs_human, newer DBs have both).
NEEDS_HUMAN_VERDICTS = ("needs_human", "escalate")
# Finding-level human adjudication vocabulary (distinct concept from the gate-verdict
# queue-bucket above): the golden annotation recorded by `finding-verdict`.
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

    def invariant_breakdown(self) -> dict:
        by_status: dict[str, int] = {}
        for status, n in self.s.execute(
                select(InvariantRegistry.status, func.count())
                .group_by(InvariantRegistry.status)):
            by_status[status] = n
        by_severity: dict[str, int] = {}
        for sev, n in self.s.execute(
                select(InvariantRegistry.severity, func.count())
                .group_by(InvariantRegistry.severity)):
            by_severity[sev] = n
        candidate_red_ids = list(self.s.scalars(
            select(InvariantRegistry.id)
            .where(InvariantRegistry.status == "candidate-red")
            .order_by(InvariantRegistry.id)))
        return {"by_status": by_status, "by_severity": by_severity,
                "candidate_red_ids": candidate_red_ids}

    def escape_timeline(self) -> list[dict]:
        """Cumulative escape count by discovery date — the ratchet population over time
        (each escape becomes a permanent check)."""
        days: dict[str, int] = {}
        for (d,) in self.s.execute(select(EscapeRegistry.discovered_at)):
            if d is not None:
                days[d.date().isoformat()] = days.get(d.date().isoformat(), 0) + 1
        out, cum = [], 0
        for day in sorted(days):
            cum += days[day]
            out.append({"date": day, "cumulative": cum})
        return out

    def list_gate_runs(self, limit: int = 50) -> list[dict]:
        """Most-recent gate runs for the Health › Verdicts drill-down table."""
        out = []
        for gr in self.s.scalars(select(GateRun).order_by(GateRun.id.desc()).limit(limit)):
            s = self.inbox_summary(gr.evidence)
            out.append({"id": gr.id, "change_ref": gr.change_ref, "verdict": gr.verdict,
                        "created_at": gr.created_at.isoformat() if gr.created_at else None,
                        "repo": s.get("repo"), "pr": s.get("pr")})
        return out

    def invariant_rows(self) -> list[dict]:
        """The invariant registry, flattened for the Health › Invariants drill-down table."""
        rows = self.s.scalars(select(InvariantRegistry)
                              .order_by(InvariantRegistry.severity, InvariantRegistry.id))
        return [{"id": r.id, "domain": r.domain, "severity": r.severity, "status": r.status,
                 "repo": r.location_repo, "path": r.location_path, "test": r.location_test,
                 "executor": r.executor_kind} for r in rows]

    def record_gate_run(self, change_ref: str, job_id: str, verdict: str,
                        evidence: dict) -> GateRun:
        run = GateRun(change_ref=change_ref, job_id=job_id, verdict=verdict,
                      evidence=evidence)
        self.s.add(run)
        self.s.commit()
        return run

    def get_gate_run(self, run_id: int) -> GateRun | None:
        return self.s.get(GateRun, run_id)

    @staticmethod
    def inbox_summary(evidence: dict | None) -> dict:
        """Project a gate_run's free-form evidence into a STABLE inbox card schema.

        gate_run.evidence has no fixed shape: production rows nest everything under
        `evidence.gates` with per-review keys, while flat fixtures put fields at the
        top level. This normalizer reads either shape and always returns the same keys
        (null where unknown), so the SPA never has to guess. Best-effort by design —
        fields the source doesn't carry come back null, not fabricated.
        """
        ev = evidence or {}
        gates = ev.get("gates")
        if isinstance(gates, str):          # some rows store gates as a JSON string
            try:
                gates = json.loads(gates)
            except (ValueError, TypeError):
                gates = None
        g = gates if isinstance(gates, dict) else ev
        if not isinstance(g, dict):
            g = {}
        # some evidence nests tier/lenses/findings one level deeper, under gates.review
        review = g.get("review") if isinstance(g.get("review"), dict) else {}
        sources = (g, review)

        def _str(*keys):
            for src in sources:
                for k in keys:
                    v = src.get(k)
                    if isinstance(v, str) and v.strip():
                        return v
            return None

        def _int(*keys):
            for src in sources:
                for k in keys:
                    v = src.get(k)
                    if isinstance(v, int) and not isinstance(v, bool):
                        return v
            return None

        # review dimensions: a list, or {completed:[...], scout_lenses:[...]}
        dims = []
        for src in sources:
            for k in ("dimensions", "lenses"):
                v = src.get(k)
                if isinstance(v, list):
                    dims = [str(x) for x in v]
                    break
                if isinstance(v, dict):
                    cand = v.get("completed") or v.get("scout_lenses")
                    if isinstance(cand, list):
                        dims = [str(x) for x in cand]
                        break
            if dims:
                break

        # findings: a list of {severity,title,...} (rich rows) or review.confirmed_findings
        findings = g.get("findings")
        if not isinstance(findings, list) and isinstance(review.get("confirmed_findings"), list):
            findings = [{"title": str(x)} for x in review["confirmed_findings"]]
        findings_total = len(findings) if isinstance(findings, list) else _int("high_sev_findings")
        top, sev_counts = [], {}
        if isinstance(findings, list):
            for f in findings:
                if not isinstance(f, dict):
                    continue
                sev = str(f.get("severity", "")).lower()
                if sev:
                    sev_counts[sev] = sev_counts.get(sev, 0) + 1
                ttl = f.get("title") or f.get("location")
                if ttl and len(top) < 3:
                    top.append({"severity": f.get("severity"), "title": str(ttl)})

        # severity summary: prefer curated review_quorum, else counts from findings
        severity = None
        rq = g.get("review_quorum")
        if isinstance(rq, dict):
            bits = []
            if isinstance(rq.get("confirmed_high"), int) and rq["confirmed_high"]:
                bits.append(f"{rq['confirmed_high']} high")
            if isinstance(rq.get("advisory"), int) and rq["advisory"]:
                bits.append(f"{rq['advisory']} advisory")
            severity = " · ".join(bits) or None
        if severity is None and sev_counts:
            order = ["high", "medium", "mid", "low", "advisory"]
            bits = [f"{sev_counts[s]} {s}" for s in order if s in sev_counts]
            bits += [f"{n} {s}" for s, n in sev_counts.items() if s not in order]
            severity = " · ".join(bits) or None

        changed_files = _int("changed_files")
        if changed_files is None and isinstance(g.get("closure"), dict):
            cf = g["closure"].get("changed_files")
            if isinstance(cf, int) and not isinstance(cf, bool):
                changed_files = cf

        # comment / PR permalink (only trust http(s))
        comment_url = _str("comment_url", "marker_url")
        if comment_url is None:
            cm = g.get("comment")
            if isinstance(cm, str) and cm.startswith("http"):
                comment_url = cm
            elif isinstance(cm, dict) and isinstance(cm.get("url"), str):
                comment_url = cm["url"]
        if comment_url and not comment_url.startswith(("http://", "https://")):
            comment_url = None

        repo = _str("repo")
        pr = g.get("pr")
        if isinstance(pr, dict):            # some rows store pr as {number: N, ...}
            pr = pr.get("number") or pr.get("pr") or pr.get("id")
        if isinstance(pr, bool) or not isinstance(pr, (int, str)):
            pr = None
        # fall back to the repo/PR embedded in a github comment/PR permalink
        if (repo is None or pr is None) and comment_url:
            m = re.search(r"github\.com/[^/]+/([^/\s#]+)/pull/(\d+)", comment_url)
            if m:
                repo = repo or m.group(1)
                pr = pr if pr is not None else int(m.group(2))
        # explicit backfill (written by the GitHub SHA->PR resolver) fills any residual gap
        bf = ev.get("_backfill")
        if isinstance(bf, dict):
            if repo is None and isinstance(bf.get("repo"), str):
                repo = bf["repo"]
            if pr is None and bf.get("pr") is not None:
                pr = bf["pr"]

        # human identity: "repo #pr", else the top finding's title
        if repo and pr is not None:
            title = f"{repo} #{pr}"
        elif top:
            title = top[0]["title"]
        else:
            title = None

        # invariants: flat ints (fixtures) or a {name: status} map
        inv_pass = inv_total = None
        if isinstance(g.get("invariants_run"), int) and not isinstance(g.get("invariants_run"), bool):
            inv_total = g["invariants_run"]
            inv_pass = g.get("invariants_pass") if isinstance(g.get("invariants_pass"), int) else None
        elif isinstance(g.get("invariants"), dict):
            vals = list(g["invariants"].values())
            inv_total = len(vals)
            inv_pass = sum(1 for x in vals if str(x).lower() == "pass")

        return {
            "title": title,
            "repo": repo,
            "pr": pr,
            "tier": _str("tier"),
            "cip": _str("cip"),
            "dimensions": dims,
            "severity": severity,
            "findings_total": findings_total,
            "top_findings": top,
            "invariants_pass": inv_pass,
            "invariants_total": inv_total,
            "changed_files": changed_files,
            "comment_url": comment_url,
            "headline": _str("summary", "headline", "change", "reason", "remedy"),
        }

    def list_needs_human(self, limit: int = 50) -> list[dict]:
        stmt = (select(GateRun)
                .where(GateRun.verdict.in_(NEEDS_HUMAN_VERDICTS))
                .order_by(GateRun.created_at.desc(), GateRun.id.desc())
                .limit(limit))
        return [
            {"id": r.id, "change_ref": r.change_ref, "job_id": r.job_id,
             "verdict": r.verdict, "evidence": r.evidence,
             "summary": self.inbox_summary(r.evidence),
             "created_at": r.created_at.isoformat()}
            for r in self.s.scalars(stmt)
        ]

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

    def escape_breakdown(self) -> list[dict]:
        stmt = (select(EscapeRegistry.root_cause_class, EscapeRegistry.status,
                       func.count())
                .group_by(EscapeRegistry.root_cause_class, EscapeRegistry.status))
        agg: dict[str, dict] = {}
        for root_cause, status, n in self.s.execute(stmt):
            slot = agg.setdefault(
                root_cause, {"root_cause_class": root_cause, "count": 0,
                             "open": 0, "closed": 0})
            slot["count"] += n
            if status in ("open", "closed"):
                slot[status] += n
        return sorted(agg.values(), key=lambda r: r["count"], reverse=True)

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
        # Report main's canonical vocabulary (pass/block/escalate) always. For dashboard
        # back-compat with databases written under the older vocabulary, additionally
        # surface a folded `needs_human` bucket (legacy `needs_human` + renamed-forward
        # `escalate`) — but only when legacy rows actually exist, so a clean DB shows just
        # the canonical keys.
        gate_by_verdict = {
            v: _count(GateRun, GateRun.verdict == v)
            for v in ("pass", "block", "escalate")
        }
        legacy_needs_human = _count(GateRun, GateRun.verdict == "needs_human")
        if legacy_needs_human:
            gate_by_verdict["needs_human"] = legacy_needs_human + gate_by_verdict["escalate"]
        return {
            "invariant_gate_count": inv_active,
            "ratchet_invariants": inv_ratchet,
            "escapes_open": esc_open,
            "escapes_closed": esc_closed,
            "ratchet_increment": esc_closed,   # 每个 closed escape 至少织出一条检查
            "gate_runs_total": gate_total,
            "gate_runs_by_verdict": gate_by_verdict,
            "mean_time_to_detection": self.mttd(),
            "unavailable": {
                "escape_rate": "needs a total-bug denominator (not tracked)",
                "tiered_review_coverage": "needs a Classifications table (not modeled in this slice)",
                "cip_conformance_pct": "use `conformance --spec-root <cowboy>`",
            },
        }

    def mttd(self) -> dict:
        # Mean time-to-detection over escapes that have both an introduced_at_ts and a
        # discovered_at. Escapes lacking the timestamp are excluded and counted honestly
        # (no fabricated interval), matching the metrics() honesty policy.
        rows = self.s.execute(
            select(EscapeRegistry.discovered_at, EscapeRegistry.introduced_at_ts)).all()
        deltas, excluded = [], 0
        for discovered_at, introduced_at_ts in rows:
            if discovered_at is not None and introduced_at_ts is not None:
                deltas.append((discovered_at - introduced_at_ts).total_seconds())
            else:
                excluded += 1
        if not deltas:
            return {"mean_days": None, "count": 0, "excluded": excluded}
        return {"mean_days": round(sum(deltas) / len(deltas) / 86400, 2),
                "count": len(deltas), "excluded": excluded}

    def verdict_timeseries(self) -> list[dict]:
        # Bucket gate runs by calendar day (UTC) and verdict. Done in Python so it
        # stays engine-agnostic (SQLite date() vs Postgres date_trunc differ).
        buckets: dict[str, dict] = {}
        for r in self.s.scalars(select(GateRun).order_by(GateRun.created_at)):
            day = r.created_at.date().isoformat()
            slot = buckets.setdefault(
                day, {"date": day, "pass": 0, "needs_human": 0, "block": 0})
            v = "needs_human" if r.verdict in NEEDS_HUMAN_VERDICTS else r.verdict
            if v in slot:
                slot[v] += 1
        return [buckets[d] for d in sorted(buckets)]

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.s.get(Meta, key)
        return row.value if row else default

    def set_meta(self, key: str, value: str) -> None:
        row = self.s.get(Meta, key)
        if row:
            row.value = value
        else:
            self.s.add(Meta(key=key, value=value))
        self.s.commit()

    def seed_authoritative_tables(self, src_session) -> tuple[int, int]:
        """用 src_session(快照)里的两张权威表替换本会话的同名表;
        gate_run / audit_log 不动。返回 (写入不变量数, 写入逃逸数)。"""
        n_inv = self._replace_table(src_session, InvariantRegistry)
        n_esc = self._replace_table(src_session, EscapeRegistry)
        return n_inv, n_esc

    def _replace_table(self, src_session, Model) -> int:
        self.s.query(Model).delete()
        rows = src_session.query(Model).all()
        for obj in rows:
            data = {c.name: getattr(obj, c.name) for c in Model.__table__.columns}
            self.s.add(Model(**data))
        return len(rows)

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

    @staticmethod
    def _job_dict(j: ReviewJob) -> dict:
        return {"id": j.id, "change_ref": j.change_ref, "repo": j.repo,
                "kind": j.kind, "status": j.status, "requested_by": j.requested_by,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                "result": j.result, "error": j.error}

    def enqueue_job(self, change_ref: str, repo: str = "node",
                    kind: str = "mechanical", requested_by: str = "dashboard") -> dict:
        job = ReviewJob(change_ref=change_ref, repo=repo, kind=kind,
                        requested_by=requested_by)
        self.s.add(job)
        self.s.commit()
        return self._job_dict(job)

    def get_job(self, job_id: int) -> dict | None:
        j = self.s.get(ReviewJob, job_id)
        return self._job_dict(j) if j else None

    def job_stats(self) -> dict:
        """Queue depth by status + the job currently being processed (if any) — powers
        the dashboard's worker/liveness panel."""
        counts = {"pending": 0, "running": 0, "done": 0, "failed": 0}
        for status, c in self.s.execute(
                select(ReviewJob.status, func.count()).group_by(ReviewJob.status)).all():
            counts[status] = c
        running = self.s.scalars(
            select(ReviewJob).where(ReviewJob.status == "running")
            .order_by(ReviewJob.started_at)).first()
        # running first (status 'running' > 'pending'), then pending oldest-first
        active = self.s.scalars(
            select(ReviewJob).where(ReviewJob.status.in_(("running", "pending")))
            .order_by(ReviewJob.status.desc(), ReviewJob.created_at).limit(12))
        return {"counts": counts, "current": self._job_dict(running) if running else None,
                "active": [self._job_dict(j) for j in active]}

    def claim_next_job(self) -> dict | None:
        # Compare-and-swap claim: read the oldest pending job, then atomically flip
        # it to running guarded by status=='pending'. If another worker won the race
        # (rowcount 0), retry with the next pending row. This is safe on SQLite and
        # guarantees no two workers claim the same job.
        while True:
            job = self.s.scalars(
                select(ReviewJob).where(ReviewJob.status == "pending")
                .order_by(ReviewJob.created_at, ReviewJob.id).limit(1)).first()
            if job is None:
                return None
            res = self.s.execute(
                update(ReviewJob)
                .where(ReviewJob.id == job.id, ReviewJob.status == "pending")
                .values(status="running", started_at=_now()))
            self.s.commit()
            if res.rowcount == 1:
                self.s.refresh(job)
                return self._job_dict(job)
            # lost the race; expire the stale row and try the next pending one
            self.s.expire(job)

    def finish_job(self, job_id: int, result: dict) -> dict:
        j = self.s.get(ReviewJob, job_id)
        if j is None:
            raise ValueError(f"job not found: {job_id}")
        j.status = "done"
        j.result = result
        j.finished_at = _now()
        self.s.commit()
        return self._job_dict(j)

    def fail_job(self, job_id: int, error: str) -> dict:
        j = self.s.get(ReviewJob, job_id)
        if j is None:
            raise ValueError(f"job not found: {job_id}")
        j.status = "failed"
        j.error = error
        j.finished_at = _now()
        self.s.commit()
        return self._job_dict(j)

    def reclaim_stale_jobs(self, older_than_s: float = 1800) -> int:
        """Fail jobs stuck in 'running' past older_than_s. A worker killed mid-job
        leaves the row 'running' forever; this clears those orphans. Returns the count."""
        cutoff = _now() - timedelta(seconds=older_than_s)
        rows = self.s.scalars(
            select(ReviewJob).where(ReviewJob.status == "running",
                                    ReviewJob.started_at < cutoff)).all()
        for j in rows:
            j.status = "failed"
            j.error = "reclaimed: worker exited before finishing this job"
            j.finished_at = _now()
        self.s.commit()
        return len(rows)

    def save_planned_event(self, event, job_id: str) -> PlannedEvent:
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("planned event job_id must be non-empty")
        row = PlannedEvent(
            job_id=job_id, kind=event.kind, repo=event.repo,
            change_ref=event.change_ref, diff_paths=list(event.diff_paths),
            labels=list(event.labels), actor=event.actor)
        self.s.merge(row)
        self.s.commit()
        return row

    def get_planned_event(self, job_id: str) -> dict | None:
        row = self.s.get(PlannedEvent, job_id)
        if row is None:
            return None
        return {
            "kind": row.kind, "repo": row.repo, "change_ref": row.change_ref,
            "diff_paths": list(row.diff_paths or []), "labels": list(row.labels or []),
            "actor": row.actor,
        }

    def close_escape_with_invariant(self, escape_id: str, spawned_check: str,
                                    invariant: dict,
                                    fix_ref: str | None = None) -> EscapeRegistry:
        """Atomically register the ratchet check and close its escape."""
        if not spawned_check:
            raise ValueError("cannot close escape without a spawned_check (棘轮纪律)")
        esc = self.s.get(EscapeRegistry, escape_id)
        if esc is None:
            raise ValueError(f"escape not found: {escape_id}")
        row = InvariantRegistry(**invariant, origin="ratchet", escape_id=escape_id)
        self.s.merge(row)
        esc.spawned_check = spawned_check
        esc.status = "closed"
        if fix_ref is not None:
            esc.fix_ref = fix_ref
        self.s.commit()
        return esc

    def open_review_run(self, **kw) -> ReviewRun:
        # New runs start open; evidence is closed out explicitly after all
        # lenses and external checks have reported their availability.
        status = kw.pop("status", "open")
        if status != "open" or status not in REVIEW_RUN_STATUSES:
            raise ValueError("new review runs must have status open")
        evidence = dict(kw.pop("evidence", {}) or {})
        plan_fields = {
            "lenses": kw.pop("expected_lenses", None),
            "commands": kw.pop("expected_commands", None),
            "external_scans": kw.pop("expected_external_scans", None),
        }
        if any(value is not None for value in plan_fields.values()):
            if any(not isinstance(value, list) or not value for value in plan_fields.values()):
                raise ValueError(
                    "review plan lenses, commands, and external_scans must be non-empty lists"
                )
            plan = {name: list(value) for name, value in plan_fields.items()}
            evidence["review_plan"] = plan
        run = ReviewRun(**kw, status=status, evidence=validate_review_evidence(evidence))
        self.s.add(run)
        self.s.commit()
        return run


    def close_review_run(self, run_id: int, status: str, evidence: dict) -> ReviewRun:
        if status not in {"complete", "degraded"}:
            raise ValueError("review run close status must be complete or degraded")
        run = self.s.get(ReviewRun, run_id)
        if run is None:
            raise ValueError(f"review run not found: {run_id}")
        if (run.status or "open") != "open":
            raise ValueError(f"review run {run_id} is already closed")
        if not isinstance(evidence, dict):
            raise ValueError("review evidence must be a JSON object")
        required = ("head_sha", "base_sha", "tree_sha", "steps", "lenses",
                    "commands", "external_scans")
        missing = [field for field in required if field not in evidence]
        if missing:
            raise ValueError("review evidence is missing required sections: " + ", ".join(missing))
        manifest = validate_review_evidence(evidence, complete=status == "complete")
        if not isinstance(manifest["steps"], dict) or set(manifest["steps"]) != REQUIRED_STEPS:
            raise ValueError(
                "review evidence steps must contain exactly "
                + ", ".join(sorted(REQUIRED_STEPS))
            )
        for field in ("head_sha", "base_sha", "tree_sha"):
            if not isinstance(manifest[field], str) or not manifest[field].strip():
                raise ValueError(f"review evidence {field} must be a non-empty string")
        if not isinstance(manifest["lenses"], dict):
            raise ValueError("review evidence lenses must be an object")
        if not all(
            field in manifest["lenses"] for field in ("expected", "returned", "missing")
        ):
            raise ValueError(
                "review evidence lenses must include expected, returned, and missing"
            )
        if not manifest["commands"]:
            raise ValueError("review evidence commands must not be empty")
        if manifest["head_sha"] != run.change_ref:
            raise ValueError(
                "review evidence head_sha must match the review run change_ref"
            )
        plan = (run.evidence or {}).get("review_plan")
        if not isinstance(plan, dict):
            raise ValueError("review runs require a review plan recorded at open")
        if plan is not None:
            supplied_plan = manifest.get("review_plan", plan)
            if supplied_plan != plan:
                raise ValueError("review evidence review_plan does not match the open plan")
            actual_names = {
                "lenses": manifest["lenses"]["expected"],
                "commands": [item["name"] for item in manifest["commands"]],
                "external_scans": [item["name"] for item in manifest["external_scans"]],
            }
            for field in ("lenses", "commands", "external_scans"):
                if set(actual_names[field]) != set(plan[field]):
                    raise ValueError(f"review evidence {field} does not match the open plan")
            manifest["review_plan"] = plan
        if status == "complete" and evidence_has_unresolved(manifest):
            raise ValueError(
                "cannot mark review run complete while evidence contains unresolved "
                "steps, commands, lenses, or external scans"
            )
        result = self.s.execute(
            update(ReviewRun)
            .where(
                ReviewRun.id == run_id,
                or_(ReviewRun.status == "open", ReviewRun.status.is_(None)),
            )
            .values(status=status, evidence=manifest)
        )
        if result.rowcount != 1:
            self.s.rollback()
            current = self.s.get(ReviewRun, run_id)
            if current is None:
                raise ValueError(f"review run not found: {run_id}")
            raise ValueError(f"review run {run_id} is already closed")
        self.s.commit()
        return self.s.get(ReviewRun, run_id)

    def review_run_snapshot(self, run_id: int) -> dict:
        run = self.s.get(ReviewRun, run_id)
        if run is None:
            raise ValueError(f"review run not found: {run_id}")
        findings = []
        for finding in self.list_findings(run_id):
            findings.append({
                "id": finding.id, "key": finding.key, "title": finding.title,
                "claim": finding.claim, "location": finding.location,
                "severity": finding.severity, "lens": finding.lens,
                "votes": finding.votes or [],
                "quorum_verdict": finding.quorum_verdict,
                "human_verdict": finding.human_verdict,
                "human_note": finding.human_note,
            })
        return {
            "run_id": run.id, "change_ref": run.change_ref, "repo": run.repo,
            "mode": run.mode, "host": run.host, "model": run.model,
            "skill_rev": run.skill_rev, "context_ref": run.context_ref,
            "status": run.status or "open", "evidence": run.evidence or {},
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "findings": findings,
        }

    def _require_open_review_run(self, run_id: int) -> ReviewRun:
        run = self.s.get(ReviewRun, run_id)
        if run is None:
            raise ValueError(f"review run not found: {run_id!r}")
        if (run.status or "open") != "open":
            raise ValueError(f"review run {run_id} is already closed")
        return run

    def record_finding(self, **kw) -> ReviewFinding:
        run_id = kw.get("run_id")
        key = kw.get("key")
        if not isinstance(run_id, int):
            raise ValueError(f"review run not found: {run_id!r}")
        self._require_open_review_run(run_id)
        if not isinstance(key, str) or not key:
            raise ValueError("review finding key must be non-empty")
        existing = self.s.scalar(select(ReviewFinding).where(
            ReviewFinding.run_id == run_id, ReviewFinding.key == key))
        if existing is not None:
            for name, value in kw.items():
                if name != "run_id" and hasattr(existing, name):
                    setattr(existing, name, value)
            self.s.commit()
            return existing
        f = ReviewFinding(**kw)
        self.s.add(f)
        try:
            self.s.commit()
        except IntegrityError:
            self.s.rollback()
            existing = self.s.scalar(select(ReviewFinding).where(
                ReviewFinding.run_id == run_id, ReviewFinding.key == key))
            if existing is None:
                raise
            for name, value in kw.items():
                if name != "run_id" and hasattr(existing, name):
                    setattr(existing, name, value)
            self.s.commit()
            return existing
        return f

    def record_findings(self, findings: list[dict]) -> list[ReviewFinding]:
        """Atomically upsert one review run's finding batch."""
        if not findings:
            return []
        run_ids = {item.get("run_id") for item in findings}
        if len(run_ids) != 1:
            raise ValueError("all findings in a batch must use one review run")
        run_id = next(iter(run_ids))
        if not isinstance(run_id, int):
            raise ValueError(f"review run not found: {run_id!r}")
        self._require_open_review_run(run_id)
        keys = [item.get("key") for item in findings]
        if any(not isinstance(key, str) or not key for key in keys):
            raise ValueError("review finding keys must be non-empty")
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate review finding key in batch")
        saved = []
        for item in findings:
            existing = self.s.scalar(select(ReviewFinding).where(
                ReviewFinding.run_id == run_id, ReviewFinding.key == item["key"]))
            if existing is None:
                existing = ReviewFinding(**item)
                self.s.add(existing)
            else:
                for name, value in item.items():
                    if name != "run_id" and hasattr(existing, name):
                        setattr(existing, name, value)
            saved.append(existing)
        self.s.commit()
        return saved

    def list_findings(self, run_id: int) -> list[ReviewFinding]:
        stmt = select(ReviewFinding).where(ReviewFinding.run_id == run_id).order_by(ReviewFinding.id)
        return list(self.s.scalars(stmt))

    def set_human_verdict(self, finding_id: int, verdict: str,
                          note: str = "") -> ReviewFinding:
        if verdict not in _HUMAN_VERDICTS:
            raise ValueError(
                f"human verdict must be one of {sorted(_HUMAN_VERDICTS)}, got {verdict!r}")
        f = self.s.get(ReviewFinding, finding_id)
        if f is None:
            raise ValueError(f"finding not found: {finding_id}")
        self._require_open_review_run(f.run_id)
        if f.human_verdict:
            raise ValueError(f"finding {finding_id} already has a human verdict")
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
