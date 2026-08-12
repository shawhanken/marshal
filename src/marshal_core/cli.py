"""Marshal 薄 CLI — skill 的确定性执行器。JSON 出入,错误非零退出。

db 路径解析为绝对 $MARSHAL_HOME/marshal.db,与 cwd 无关。
"""
import argparse
from contextlib import contextmanager
import difflib
import json
import os
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path

import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marshal_core.knowledge.models import ensure_schema
from marshal_core.knowledge.store import Store
from marshal_core.onboard.estimate import estimate_cost
from marshal_core.onboard.detect import detect_repo
from marshal_core.onboard.report import tech_debt_signals
from marshal_core.plangate.service import plan_review, isolated_store
from marshal_core.review import (
    aggregate_review, assign_refute_lenses, ratchet_lenses, verify_findings,
)
from marshal_pack_cowboy.pack import CowboyPack

_PACK = CowboyPack()

_SKILL_HOSTS = {
    "claude": (Path(".claude/skills"), Path(".claude/skills")),
    "codex": (Path(".agents/skills"), Path(".agents/skills")),
}
_SKILLS_BY_HOST = {
    "claude": ("marshal", "onboard", "plan-cost"),
    "codex": ("marshal", "onboard", "plan-cost", "marshal-pr-sweep"),
}


def _symlink_points_to(link: Path, target: Path) -> bool:
    """Whether link is a symlink already managed by this Marshal checkout."""
    if not link.is_symlink():
        return False
    try:
        return link.resolve(strict=False) == target.resolve(strict=False)
    except (OSError, RuntimeError):
        return False


def _marshal_checkout_for_skill_link(link: Path, host: str, name: str) -> Path | None:
    """Return the checkout root for a live link to the same bundled Marshal skill."""
    if not link.is_symlink():
        return None
    try:
        target = link.resolve(strict=True)
        checkout = target.parents[2]
    except (IndexError, OSError, RuntimeError):
        return None
    source_root, _ = _SKILL_HOSTS[host]
    if target != (checkout / source_root / name).resolve(strict=False):
        return None
    pyproject = checkout / "pyproject.toml"
    skill_file = target / "SKILL.md"
    if not pyproject.is_file() or not skill_file.is_file():
        return None
    if not (checkout / "src" / "marshal_core" / "cli.py").is_file():
        return None
    try:
        with pyproject.open("rb") as f:
            project = tomllib.load(f)
        skill_text = skill_file.read_text(encoding="utf-8")
        _, frontmatter, _ = skill_text.split("---", 2)
        metadata = yaml.safe_load(frontmatter)
    except (OSError, ValueError, yaml.YAMLError):
        return None
    project_metadata = project.get("project")
    if not isinstance(project_metadata, dict) or project_metadata.get("name") != "marshal":
        return None
    if not isinstance(metadata, dict) or metadata.get("name") != name:
        return None
    return checkout


@contextmanager
def _setup_lock(user_home: Path):
    """Serialize setup runs on POSIX and Windows."""
    user_home.mkdir(parents=True, exist_ok=True)
    lock_path = user_home / ".marshal-setup.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if os.name == "nt":
            import msvcrt
            import time

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write("\0")
                lock_file.flush()
            while True:
                try:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _replace_skill_link(link: Path, source: Path) -> None:
    """Atomically replace a recognized old Marshal link with the current source."""
    temp = link.with_name(f".{link.name}.marshal-{uuid.uuid4().hex}")
    try:
        temp.symlink_to(source, target_is_directory=True)
        os.replace(temp, link)
    finally:
        temp.unlink(missing_ok=True)


def _marshal_home() -> Path:
    env = os.environ.get("MARSHAL_HOME")
    if env:
        return Path(env)
    # cli.py 在 <home>/src/marshal_core/cli.py
    return Path(__file__).resolve().parents[2]


def _db_url() -> str:
    if os.environ.get("MARSHAL_DB"):
        return os.environ["MARSHAL_DB"]
    return f"sqlite:///{_marshal_home() / 'marshal.db'}"


def _session():
    engine = create_engine(_db_url())
    ensure_schema(engine)
    return sessionmaker(bind=engine)()


def _emit(obj) -> int:
    print(json.dumps(obj, ensure_ascii=False))
    return 0


def _fail(msg: str) -> int:
    print(json.dumps({"error": msg}, ensure_ascii=False))
    return 1


def cmd_classify(a) -> int:
    scope = {"repo": a.repo, "diff_paths": a.paths, "diff_text": a.diff_text or "",
             "labels": a.labels or []}
    # Whole-file content for .github/workflows/** so the CI threat model can reason over
    # constructs outside the 3-line diff window (P2). --workflow-file path=FILE repeated.
    wf = {}
    for spec in (a.workflow_files or []):
        if "=" not in spec:
            return _fail(f"--workflow-file expects path=localfile, got: {spec}")
        rel, local = spec.split("=", 1)
        try:
            wf[rel] = Path(local).read_text(encoding="utf-8")
        except OSError as e:
            return _fail(f"cannot read workflow file {local}: {e}")
    if wf:
        scope["workflow_files"] = wf
    return _emit(_PACK.classify_detailed(scope))


def cmd_ci_scan(a) -> int:
    """P0: deterministic CI-security backstop via zizmor (GitHub Actions auditor).

    Runs `zizmor --format json` over the given workflow files and normalizes findings.
    If zizmor is absent/errors, emits a degraded record (non-zero) so the gate records
    degraded → escalate rather than a false pass (降级不谎报)."""
    import subprocess

    binary = _resolve_zizmor(a.zizmor_bin)
    if binary is None:
        print(json.dumps({"error": "zizmor not installed", "degraded": True,
                          "tool": a.zizmor_bin or "zizmor",
                          "hint": "install zizmor into the marshal venv: "
                                  "`<venv>/bin/pip install zizmor` (or pipx/uv/cargo); "
                                  "then ci-scan finds it automatically"},
                         ensure_ascii=False))
        return 1
    if not a.paths:
        return _fail("ci-scan needs --paths <workflow files>")
    try:
        proc = subprocess.run([binary, "--format", "json", *a.paths],
                              capture_output=True, text=True, timeout=a.timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(json.dumps({"error": f"zizmor invocation failed: {e}", "degraded": True},
                         ensure_ascii=False))
        return 1
    findings = _normalize_zizmor(proc.stdout)
    if findings is None:
        print(json.dumps({"error": "could not parse zizmor output", "degraded": True,
                          "raw_stderr": proc.stderr[:2000]}, ensure_ascii=False))
        return 1
    sev_rank = {"high": 3, "medium": 2, "low": 1, "informational": 0, "unknown": 0}
    by_sev = {}
    for f in findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    worst = max((sev_rank.get(f["severity"], 0) for f in findings), default=0)
    return _emit({"tool": "zizmor", "scanned": a.paths, "count": len(findings),
                  "by_severity": by_sev,
                  "worst_severity": next((s for s, r in sev_rank.items() if r == worst),
                                         "none") if findings else "none",
                  "findings": findings})


def _resolve_zizmor(explicit):
    """Find the zizmor binary. Explicit path wins; otherwise prefer the one installed
    next to the running interpreter (the marshal venv's bin, where `pip install zizmor`
    puts it) so the gate works without PATH fiddling, then fall back to PATH."""
    import shutil

    if explicit:
        return explicit if (Path(explicit).exists() or shutil.which(explicit)) else None
    venv_bin = Path(sys.executable).parent / "zizmor"
    if venv_bin.exists():
        return str(venv_bin)
    return shutil.which("zizmor")


def _normalize_zizmor(stdout: str):
    """zizmor JSON (array of findings) → [{id, severity, path, location, message}].
    Returns None on parse failure. Matches the zizmor 1.x schema: severity under
    `.determinations.severity`; path under `.locations[0].symbolic.key.{Local|Remote}`;
    line under `.locations[0].concrete.location.start_point.row`. Falls back gracefully."""
    try:
        data = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        data = data.get("findings") or data.get("results") or []
    if not isinstance(data, list):
        return None
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        sev = (item.get("determinations") or {}).get("severity") \
            or item.get("severity") or item.get("level") or "unknown"
        path, location = "", ""
        locs = item.get("locations") or []
        if locs and isinstance(locs[0], dict):
            sym = locs[0].get("symbolic") or {}
            key = sym.get("key") or {}
            if isinstance(key, dict):
                loc = key.get("Local") or key.get("Remote") or {}
                path = loc.get("given_path") or loc.get("path") or ""
            conc = (locs[0].get("concrete") or {}).get("location") or {}
            row = (conc.get("start_point") or {}).get("row")
            if row is not None:
                location = f"line {row}"
        out.append({"id": item.get("ident") or item.get("rule") or item.get("id") or "?",
                    "severity": str(sev).lower(),
                    "path": path or item.get("path", ""),
                    "location": location,
                    "message": item.get("desc") or item.get("message") or item.get("title", "")})
    return out


def cmd_review_quorum(a) -> int:
    # ③ 把多视角 review 发现聚合成 quorum 结论 (去重/计票/高危升 escalate)。
    return _emit(aggregate_review(json.loads(a.findings_json), quorum=a.quorum,
                                  proximity=a.proximity))


def cmd_review_verify(a) -> int:
    # ③ 对抗式验证二段: 按 skeptic 投票裁决每条发现 (default-to-refute)。
    # --run-id: ⑧ 顺手把每条发现的裁决链落 review trace (不带则行为不变, 纯函数)。
    items = json.loads(a.votes_json)
    keys = [item.get("key") for item in items]
    if any(not isinstance(key, str) or not key for key in keys):
        raise ValueError("every review finding must have a non-empty key")
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate review finding key in review-verify input")
    out = verify_findings(items)
    if a.run_id is not None:
        details = ({d["key"]: d for d in json.loads(a.findings_json)}
                   if a.findings_json else {})
        bucket = {row["key"]: name
                  for name in ("survived", "killed", "unverified")
                  for row in out[name]}
        findings = []
        for it in items:
            key = it.get("key") or ""
            d = details.get(key, {})
            findings.append({
                "run_id": a.run_id, "key": key,
                "severity": it.get("severity", ""),
                "votes": it.get("votes", []) or [],
                "quorum_verdict": bucket.get(key, ""),
                "title": d.get("title", ""), "claim": d.get("claim", ""),
                "location": d.get("location", ""), "lens": d.get("lens", ""),
            })
        s = _session()
        try:
            Store(s).record_findings(findings)
        finally:
            s.close()
    return _emit(out)


def _detect_skill_rev() -> str:
    # prompt-as-program: record the full checkout revision and flag tracked or
    # untracked skill files as dirty provenance.
    root = Path(__file__).resolve().parents[2]
    try:
        rev = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        if rev.returncode != 0 or not rev.stdout.strip():
            return ""
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain",
             "--untracked-files=all", "--",
             ".agents/skills/marshal", ".claude/skills/marshal"],
            capture_output=True, text=True, timeout=10)
        suffix = "-dirty" if dirty.stdout else ""
        return rev.stdout.strip() + suffix
    except Exception:
        return ""


def cmd_review_run_open(a) -> int:
    # ⑧ 开一次 review trace run: 落 provenance, 返回 run_id 供后续 review-verify 关联。
    skill_rev = a.skill_rev or _detect_skill_rev()
    s = _session()
    try:
        run = Store(s).open_review_run(
            change_ref=a.change_ref, repo=a.repo, mode=a.mode,
            host=a.host, model=a.model, skill_rev=skill_rev,
            context_ref=a.context_ref)
        return _emit({"run_id": run.id, "skill_rev": skill_rev, "status": run.status})
    finally:
        s.close()


def cmd_review_run_close(a) -> int:
    """Close a trace only after recording its reproducible evidence manifest."""
    manifest = json.loads(a.evidence_json)
    s = _session()
    try:
        run = Store(s).close_review_run(a.run_id, a.status, manifest)
        return _emit({"run_id": run.id, "status": run.status,
                      "evidence": run.evidence or {}})
    finally:
        s.close()


def cmd_review_run_show(a) -> int:
    s = _session()
    try:
        return _emit(Store(s).review_run_snapshot(a.run_id))
    finally:
        s.close()


def cmd_finding_verdict(a) -> int:
    # ⑧ 人类终审补录 — trace 数据的金标注出口。
    s = _session()
    try:
        f = Store(s).set_human_verdict(a.finding_id, a.verdict, a.note)
        return _emit({"finding_id": f.id, "human_verdict": f.human_verdict})
    except ValueError as e:
        return _fail(str(e))
    finally:
        s.close()


def cmd_refute_lenses(a) -> int:
    # ③ 二段 skeptic 视角多样化: 给 N 个 skeptic 轮转分配互异 refute lens (领域无关)。
    return _emit({"count": a.count, "lenses": assign_refute_lenses(a.count)})


def cmd_review_lenses(a) -> int:
    # ③ 发出聚焦 review 视角集 (name+prompt): pack review_plan (tier 基集 + 路径触发) +
    # security-hazard 视角 + 可选 ratchet 探针 (--ratchet-top N>0 = deep 模式)。让 review
    # 步确定性拿到 prompt, 不靠 skill 脑补;deep 据此 scout→prove。
    scope = {"repo": a.repo, "diff_paths": a.paths, "labels": a.labels or []}
    base = _PACK.review_plan(scope)
    hazards = [{"name": f"security-hazard:{h['id']}", "prompt": h["prompt"]}
               for h in _PACK.security_hazards(scope)]
    ratchet = []
    if a.ratchet_top and a.ratchet_top > 0:
        s = _session()
        try:
            rows = [{"root_cause_class": e.root_cause_class,
                     "description": e.description, "change_ref": e.change_ref}
                    for e in Store(s).list_escapes(domain_pack=a.ratchet_domain_pack)]
        finally:
            s.close()
        ratchet = [{"name": lp["name"], "prompt": lp["prompt"]}
                   for lp in ratchet_lenses(rows, max_lenses=a.ratchet_top)]
    out = {"base": base, "hazards": hazards, "ratchet": ratchet,
           "all": base + hazards + ratchet}
    # 空逃逸史是完整的“没有历史探针”结果，不是工具失败。deep 可用通用 fallback
    # lenses 补足 scout 多样性，同时如实呈现没有 ratchet 样本。
    if a.ratchet_top and a.ratchet_top > 0 and not ratchet:
        out["ratchet_note"] = (
            "ratchet requested but 0 historical probes were available "
            "(empty escape DB / domain_pack)"
        )
    if not a.paths:
        out["paths_note"] = "no --paths: only tier base lenses (no path-triggered views)"
    return _emit(out)


def cmd_ratchet_lenses(a) -> int:
    # ③ (deep) 把逃逸历史投成定向 review 视角: 每个复发根因类 = 一条"是否重新引入"探针。
    s = _session()
    try:
        escs = Store(s).list_escapes(domain_pack=a.domain_pack)
        rows = [{"root_cause_class": e.root_cause_class, "description": e.description,
                 "change_ref": e.change_ref} for e in escs]
        lenses = ratchet_lenses(rows, max_lenses=a.max_lenses,
                                samples_per_class=a.samples)
        return _emit({"count": len(lenses), "escapes": len(rows), "lenses": lenses})
    finally:
        s.close()


def cmd_spec_source(a) -> int:
    # 把 spec_ref 标签 (如 CIP-3 / WP) 解析到正文源 (repo + path_glob),供 skill JIT 读取。
    return _emit({"ref": a.ref, "source": _PACK.resolve_spec_ref(a.ref)})


def cmd_spec_requirements(a) -> int:
    # ⑤: 解析一个 spec_ref 的正文,抽 RFC2119 候选 requirement (要求级 conformance 分母侧)。
    import glob
    src = _PACK.resolve_spec_ref(a.ref)
    if src is None:
        return _fail(f"unresolvable spec_ref: {a.ref}")
    hits = sorted(glob.glob(os.path.join(a.spec_root, src["path_glob"])))
    if not hits:
        return _fail(f"spec source not found: {os.path.join(a.spec_root, src['path_glob'])}")
    with open(hits[0], encoding="utf-8") as fh:
        reqs = _PACK.parse_spec_requirements(fh.read())
    levels = {"must": 0, "should": 0, "may": 0}
    for r in reqs:
        levels[r["level"]] += 1
    return _emit({"ref": a.ref, "source": os.path.relpath(hits[0], a.spec_root),
                  "counts": levels, "total": len(reqs), "requirements": reqs})


def cmd_conformance(a) -> int:
    # ⑤ conformance 矩阵: spec → 覆盖它的不变量。可选 --spec-root 枚举全 CIP 集,
    # 算出未被任何不变量覆盖的 CIP (gap),即 §7 conformance% 的分母侧。
    matrix = _PACK.conformance_matrix()
    out = {"covered": matrix, "specs_covered": sorted(matrix)}
    if a.spec_root:
        import glob
        import re as _re
        cip_dir = os.path.join(a.spec_root, "docs", "cips")
        per_cip = []
        for f in glob.glob(os.path.join(cip_dir, "cip-*.md")):
            m = _re.match(r"cip-(\d+)-", os.path.basename(f))
            if not m:
                continue
            ref = f"CIP-{int(m.group(1))}"
            with open(f, encoding="utf-8") as fh:
                musts = sum(1 for r in _PACK.parse_spec_requirements(fh.read())
                            if r["level"] == "must")
            per_cip.append({"cip": ref, "must_requirements": musts,
                            "invariants": len(matrix.get(ref, [])),
                            "covered": ref in matrix})
        all_cips = {c["cip"] for c in per_cip}
        covered_cips = {c["cip"] for c in per_cip if c["covered"]}
        uncovered = sorted(all_cips - covered_cips, key=lambda s: int(s.split("-")[1]))
        pct = round(100 * len(covered_cips) / len(all_cips), 1) if all_cips else 0.0
        # 排序: 欠覆盖优先 (无不变量在前, 然后 MUST 越多越靠前) —— 即最该补的网眼。
        per_cip.sort(key=lambda c: (c["invariants"] > 0, -c["must_requirements"]))
        out["cip_total"] = len(all_cips)
        out["cip_covered"] = len(covered_cips)
        out["cip_uncovered"] = uncovered
        out["cip_conformance_pct"] = pct
        out["per_cip"] = per_cip
    return _emit(out)


def cmd_invariants(a) -> int:
    scope = {"repo": a.repo, "diff_paths": a.paths}
    invs = _PACK.list_invariants(scope)
    return _emit([
        {"id": i.id, "severity": i.severity, "executor_kind": i.executor_kind,
         "location_repo": i.location_repo, "location_path": i.location_path,
         "location_test": i.location_test, "run_command": i.run_command}
        for i in invs
    ])


def cmd_ratchet_open(a) -> int:
    s = _session()
    try:
        esc = Store(s).open_escape(
            id=a.escape_id, description=a.desc, root_cause_class=a.root_cause,
            change_ref=a.change_ref, missed_by_run_id=a.missed_by_run)
        return _emit({"escape_id": esc.id})
    finally:
        s.close()


def cmd_ratchet_close(a) -> int:
    if not a.spawned_check:
        return _fail("spawned_check is required to close an escape (棘轮纪律)")
    inv = json.loads(a.inv_json)
    inv.setdefault("domain_pack", "cowboy")  # InvariantRegistry.domain_pack 非空
    # 知识核只存引用(repo+path+test-name),不存可执行命令(spec §3.4);
    # 丢弃 InvariantDef 上有、但 InvariantRegistry 表没有的字段(如 run_command)。
    _REGISTRY_FIELDS = {"id", "domain_pack", "domain", "spec_ref", "executor_kind",
                        "location_repo", "location_path", "location_test", "severity",
                        "status"}
    inv = {k: v for k, v in inv.items() if k in _REGISTRY_FIELDS}
    s = _session()
    try:
        store = Store(s)
        store.close_escape_with_invariant(
            a.escape_id, spawned_check=a.spawned_check, invariant=inv,
            fix_ref=a.fix_ref)
        return _emit({"ok": True, "escape_id": a.escape_id,
                      "spawned_check": a.spawned_check})
    finally:
        s.close()


def cmd_gate_record(a) -> int:
    gates = json.loads(a.evidence_json)
    s = _session()
    try:
        store = Store(s)
        run = store.record_gate_run(change_ref=a.change_ref, job_id=a.change_ref,
                                    verdict=a.verdict, evidence={"gates": gates})
        store.audit(event="gate_decision", actor="marshal-skill",
                    decision=a.verdict, refs={"change_ref": a.change_ref})
        return _emit({"run_id": run.id})
    finally:
        s.close()


def cmd_metrics(a) -> int:
    # ⑦ 从知识核聚合方法论指标 (按需报告)。
    s = _session()
    try:
        return _emit(Store(s).metrics())
    finally:
        s.close()


def _parse_repo_roots(specs):
    """--repo-root repo=path 列表 → dict;缺 '=' 给清晰报错 (与其它命令风格一致)。"""
    roots = {}
    for kv in (specs or []):
        if "=" not in kv:
            raise ValueError(f"--repo-root expects repo=path, got: {kv}")
        repo, path = kv.split("=", 1)
        roots[repo] = path
    return roots


def _require_derive_paths(a):
    """Validate derive-command path inputs (concepts-dir + user-provided repo-root
    paths). Typo → hard fail, never a silently-wrong signal (Marshal N1). Returns roots."""
    if not Path(a.concepts_dir).is_dir():
        raise ValueError(f"--concepts-dir not a directory: {a.concepts_dir}")
    roots = _parse_repo_roots(a.repo_root)
    for repo, path in roots.items():
        if not Path(path).is_dir():
            raise ValueError(f"--repo-root path not a directory: {repo}={path}")
    return roots


def cmd_concept_tree(a) -> int:
    roots = _require_derive_paths(a)           # fail-fast on typo'd paths (Marshal N1)
    with isolated_store(a.concepts_dir, a.domain_pack, roots) as store:
        return _emit(store.concept_tree(a.domain_pack))


def cmd_concept_list(a) -> int:
    roots = _require_derive_paths(a)
    with isolated_store(a.concepts_dir, a.domain_pack, roots) as store:
        return _emit([{"id": c.id, "importance": c.importance, "confidence": c.confidence,
                       "doc_only": c.doc_only, "parent_id": c.parent_id}
                      for c in store.list_concepts(a.domain_pack)])


def cmd_onboard_estimate(a) -> int:
    if not Path(a.repo).is_dir():                # typo → $0 是误导性的成本门, 先挡住
        return _fail(f"--repo not a directory: {a.repo}")
    return _emit(estimate_cost(a.repo))


def cmd_onboard_detect(a) -> int:
    if not Path(a.repo).is_dir():
        return _fail(f"--repo not a directory: {a.repo}")
    return _emit(detect_repo(a.repo))


def cmd_onboard_report(a) -> int:
    # 只读信号报告 → 隔离内存 DB(深审 F1: 不 mutate 共享缓存)。路径 typo 仍 fail-fast:
    # typo 的 concepts-dir/repo-root 会让 verify_anchors 全失败 → 每个高重要性概念被误报成
    # unanchored_high(报告头号债信号被静默反转)—— _require_derive_paths 已含该校验。
    roots = _require_derive_paths(a)
    with isolated_store(a.concepts_dir, a.domain_pack, roots) as store:
        return _emit(tech_debt_signals(store, a.domain_pack))


def cmd_plan_cost(a) -> int:
    if not Path(a.touches).is_file():
        return _fail(f"--touches not a file: {a.touches}")
    with open(a.touches, encoding="utf-8") as f:
        touches = json.load(f)
    return _emit(plan_review(a.concepts_dir, _parse_repo_roots(a.repo_root),
                             a.domain_pack, touches))


def _git_output(repo_root: Path, *args: str) -> str:
    import subprocess

    proc = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _untracked_patch(repo_root: Path, rel_path: str) -> str:
    path = repo_root / rel_path
    if path.is_symlink():
        data = os.readlink(path).encode()
    else:
        data = path.read_bytes()
    if b"\0" in data:
        return f"diff --git a/{rel_path} b/{rel_path}\nBinary file b/{rel_path} added\n"
    lines = data.decode("utf-8", errors="replace").splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            [],
            lines,
            fromfile="/dev/null",
            tofile=f"b/{rel_path}",
        )
    )


def _default_worktree_base(repo_root: Path) -> tuple[str, str]:
    """Find an auditable comparison base without silently falling back to HEAD."""
    remote_refs = _git_output(
        repo_root,
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/remotes",
    ).splitlines()
    local_refs = _git_output(
        repo_root,
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/heads",
    ).splitlines()
    try:
        current_branch = _git_output(repo_root, "symbolic-ref", "--short", "HEAD").strip()
    except ValueError:
        current_branch = ""

    candidates = [ref for ref in remote_refs if ref.endswith("/HEAD")]
    for branch_name in ("main", "devnet", "master", "trunk"):
        candidates.extend(ref for ref in remote_refs if ref.endswith(f"/{branch_name}"))
        candidates.extend(
            ref for ref in local_refs
            if ref == branch_name and ref != current_branch
        )

    for ref in dict.fromkeys(candidates):
        try:
            base = _git_output(repo_root, "merge-base", "HEAD", ref).strip()
        except ValueError:
            continue
        if base:
            return base, ref
    raise ValueError(
        "cannot determine review base; pass --base or configure a remote default "
        "branch (remote HEAD, main, devnet, master, or trunk)"
    )


def cmd_worktree_diff(a) -> int:
    """Collect one coherent local diff including committed, dirty, and untracked files."""
    repo_root = Path(a.repo_root).resolve()
    if not (repo_root / ".git").exists():
        return _fail(f"--repo-root is not a git checkout: {repo_root}")
    try:
        head = _git_output(repo_root, "rev-parse", "HEAD").strip()
        base = a.base
        if base:
            base = _git_output(repo_root, "rev-parse", f"{base}^{{commit}}").strip()
            base_source = "explicit"
        else:
            base, base_source = _default_worktree_base(repo_root)

        tracked_raw = _git_output(repo_root, "diff", "--name-only", "-z", base, "--")
        untracked_raw = _git_output(
            repo_root, "ls-files", "--others", "--exclude-standard", "-z"
        )
        tracked_paths = [p for p in tracked_raw.split("\0") if p]
        untracked_paths = [p for p in untracked_raw.split("\0") if p]
        paths = list(dict.fromkeys([*tracked_paths, *untracked_paths]))
        tracked_diff = _git_output(repo_root, "diff", "--binary", base, "--")
        untracked_diff = "".join(_untracked_patch(repo_root, p) for p in untracked_paths)
        dirty = bool(
            _git_output(repo_root, "status", "--porcelain", "--untracked-files=normal")
        )
    except (OSError, ValueError) as e:
        return _fail(str(e))

    return _emit({
        "repo_root": str(repo_root),
        "base": base,
        "base_source": base_source,
        "head": head,
        "change_ref": f"{head}+worktree" if dirty else head,
        "dirty": dirty,
        "invariant_checkout": "worktree" if dirty else "head",
        "tracked_paths": tracked_paths,
        "untracked_paths": untracked_paths,
        "paths": paths,
        "diff_text": tracked_diff + untracked_diff,
    })


def cmd_setup(a) -> int:
    marshal_home = _marshal_home().resolve()
    user_home = Path(os.path.expanduser("~"))
    hosts = tuple(dict.fromkeys(a.host or _SKILL_HOSTS))

    install_plan = []
    missing = []
    for host in hosts:
        source_root, install_root = _SKILL_HOSTS[host]
        for name in _SKILLS_BY_HOST[host]:
            source = marshal_home / source_root / name
            link = user_home / install_root / name
            if not (source / "SKILL.md").is_file():
                missing.append(str(source / "SKILL.md"))
            install_plan.append((host, name, source, link))
    if missing:
        return _fail("missing bundled skill(s): " + ", ".join(missing))

    # Each destination is independent: a Codex conflict must not regress the legacy
    # Claude install path. The lock keeps concurrent setup processes from observing
    # and then rolling back each other's links.
    installed = []
    conflicts = []
    errors = []
    try:
        with _setup_lock(user_home):
            for host, name, source, link in install_plan:
                parent = link.parent
                bad_parent = None
                while parent != user_home:
                    if ((parent.exists() or parent.is_symlink()) and not parent.is_dir()):
                        bad_parent = parent
                        break
                    parent = parent.parent
                if bad_parent is not None:
                    conflicts.append({"host": host, "name": name, "path": str(link),
                                      "reason": f"parent is not a directory: {bad_parent}"})
                    continue

                if _symlink_points_to(link, source):
                    status = "unchanged"
                elif _marshal_checkout_for_skill_link(link, host, name) is not None:
                    try:
                        _replace_skill_link(link, source)
                    except OSError as e:
                        errors.append({"host": host, "name": name, "path": str(link),
                                       "reason": str(e)})
                        continue
                    status = "migrated"
                elif link.is_symlink() or link.exists():
                    conflicts.append({"host": host, "name": name, "path": str(link),
                                      "reason": "destination is not managed by Marshal"})
                    continue
                else:
                    try:
                        link.parent.mkdir(parents=True, exist_ok=True)
                        link.symlink_to(source, target_is_directory=True)
                    except OSError as e:
                        errors.append({"host": host, "name": name, "path": str(link),
                                       "reason": str(e)})
                        continue
                    status = "linked"
                installed.append({"host": host, "name": name, "symlink": str(link),
                                  "target": str(source), "status": status})
    except OSError as e:
        return _fail(f"could not install skill links: {e}")

    try:
        import marshal_pack_cowboy.pack  # noqa: F401
        import_ok = True
    except Exception:
        import_ok = False

    zizmor = _resolve_zizmor(None)
    hints = []
    if not import_ok:
        hints.append("run: pip install -e . in marshal venv")
    if zizmor is None:
        hints.append("CI gate degraded: install zizmor — `pip install -e .[ci]` "
                     "(or pip install zizmor) in the marshal venv")
    # Preserve the original fields for callers that consumed the Claude-only response.
    legacy = next((item for item in installed
                   if item["host"] == "claude" and item["name"] == "marshal"), None)
    ok = not conflicts and not errors
    payload = {"ok": ok,
               "symlink": legacy["symlink"] if legacy else None,
               "target": legacy["target"] if legacy else None,
               "installed": installed,
               "conflicts": conflicts,
               "errors": errors,
               "import_ok": import_ok, "zizmor": zizmor or "MISSING",
               "hint": "; ".join(hints) or None}
    if not ok:
        payload["error"] = (
            "some skill destinations could not be installed; "
            "safe destinations were preserved"
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 1
    return _emit(payload)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="marshal")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("classify")
    c.add_argument("--repo", required=True)
    c.add_argument("--paths", nargs="*", default=[])
    c.add_argument("--diff-text", dest="diff_text", default="")
    c.add_argument("--labels", nargs="*", default=[])
    c.add_argument("--workflow-file", dest="workflow_files", action="append", default=[],
                   help="repeatable repo_path=localfile; whole .github/workflows content "
                        "for the CI threat model (P2 — sees constructs outside the diff)")
    c.set_defaults(func=cmd_classify)

    cs = sub.add_parser("ci-scan")
    cs.add_argument("--paths", nargs="*", default=[],
                    help="workflow files to audit with zizmor")
    cs.add_argument("--zizmor-bin", dest="zizmor_bin", default=None,
                    help="zizmor binary (default: zizmor on PATH)")
    cs.add_argument("--timeout", type=int, default=120)
    cs.set_defaults(func=cmd_ci_scan)

    iv = sub.add_parser("invariants")
    iv.add_argument("--repo", required=True)
    iv.add_argument("--paths", nargs="*", default=[])
    iv.set_defaults(func=cmd_invariants)

    rq = sub.add_parser("review-quorum")
    rq.add_argument("--findings-json", dest="findings_json", required=True)
    rq.add_argument("--quorum", type=int, default=2)
    rq.add_argument("--proximity", type=int, default=10,
                    help="merge same-file findings within N lines (0 = exact-line only)")
    rq.set_defaults(func=cmd_review_quorum)

    rv = sub.add_parser("review-verify")
    rv.add_argument("--votes-json", dest="votes_json", required=True)
    rv.add_argument("--run-id", dest="run_id", type=int, default=None)
    rv.add_argument("--findings-json", dest="findings_json", default="")
    rv.set_defaults(func=cmd_review_verify)

    rro = sub.add_parser("review-run-open")
    rro.add_argument("--change-ref", dest="change_ref", required=True)
    rro.add_argument("--repo", default="")
    rro.add_argument("--mode", default="regular", choices=["regular", "deep"])
    rro.add_argument("--host", default="")
    rro.add_argument("--model", default="")
    rro.add_argument("--skill-rev", dest="skill_rev", default="")
    rro.add_argument("--context-ref", dest="context_ref", default="")
    rro.set_defaults(func=cmd_review_run_open)

    rrc = sub.add_parser("review-run-close")
    rrc.add_argument("--run-id", dest="run_id", type=int, required=True)
    rrc.add_argument("--status", required=True, choices=["complete", "degraded"])
    rrc.add_argument("--evidence-json", dest="evidence_json", required=True)
    rrc.set_defaults(func=cmd_review_run_close)

    rrs = sub.add_parser("review-run-show")
    rrs.add_argument("--run-id", dest="run_id", type=int, required=True)
    rrs.set_defaults(func=cmd_review_run_show)

    fv = sub.add_parser("finding-verdict")
    fv.add_argument("--finding-id", dest="finding_id", type=int, required=True)
    fv.add_argument("--verdict", required=True,
                    choices=["accepted", "rejected", "modified"])
    fv.add_argument("--note", default="")
    fv.set_defaults(func=cmd_finding_verdict)

    rl = sub.add_parser("refute-lenses")
    rl.add_argument("--count", type=int, required=True)
    rl.set_defaults(func=cmd_refute_lenses)

    rvl = sub.add_parser("review-lenses")
    rvl.add_argument("--repo", required=True)
    rvl.add_argument("--paths", nargs="*", default=[])
    rvl.add_argument("--labels", nargs="*", default=[])
    rvl.add_argument("--ratchet-top", dest="ratchet_top", type=int, default=0,
                     help="append N ratchet probes (deep mode); 0 = regular (base only)")
    rvl.add_argument("--ratchet-domain-pack", dest="ratchet_domain_pack", default=None)
    rvl.set_defaults(func=cmd_review_lenses)

    rtl = sub.add_parser("ratchet-lenses")
    rtl.add_argument("--domain-pack", dest="domain_pack", default=None,
                     help="filter escapes by domain_pack (default: all)")
    rtl.add_argument("--max-lenses", dest="max_lenses", type=int, default=8)
    rtl.add_argument("--samples", type=int, default=3,
                     help="precedent descriptions embedded per lens")
    rtl.set_defaults(func=cmd_ratchet_lenses)

    ss = sub.add_parser("spec-source")
    ss.add_argument("--ref", required=True)
    ss.set_defaults(func=cmd_spec_source)

    sr = sub.add_parser("spec-requirements")
    sr.add_argument("--ref", required=True)
    sr.add_argument("--spec-root", dest="spec_root", required=True,
                    help="path to the cowboy repo root containing docs/{cips,whitepaper}")
    sr.set_defaults(func=cmd_spec_requirements)

    cf = sub.add_parser("conformance")
    cf.add_argument("--spec-root", dest="spec_root", default=None,
                    help="path to the cowboy repo root; enables CIP gap/percent reporting")
    cf.set_defaults(func=cmd_conformance)

    ro = sub.add_parser("ratchet-open")
    ro.add_argument("--escape-id", dest="escape_id", required=True)
    ro.add_argument("--desc", required=True)
    ro.add_argument("--root-cause", dest="root_cause", default="")
    ro.add_argument("--change-ref", dest="change_ref", default=None)
    ro.add_argument("--missed-by-run", dest="missed_by_run", type=int, default=None,
                    help="review_run id that reviewed this change but missed the bug")
    ro.set_defaults(func=cmd_ratchet_open)

    rc = sub.add_parser("ratchet-close")
    rc.add_argument("--escape-id", dest="escape_id", required=True)
    rc.add_argument("--spawned-check", dest="spawned_check", default="")
    rc.add_argument("--inv-json", dest="inv_json", required=True)
    rc.add_argument("--fix-ref", dest="fix_ref", default=None,
                    help="commit that fixed the escaped bug")
    rc.set_defaults(func=cmd_ratchet_close)

    gr = sub.add_parser("gate-record")
    gr.add_argument("--change-ref", dest="change_ref", required=True)
    gr.add_argument("--verdict", required=True,
                    choices=["pass", "block", "escalate"])
    gr.add_argument("--evidence-json", dest="evidence_json", default="[]")
    gr.set_defaults(func=cmd_gate_record)

    mt = sub.add_parser("metrics")
    mt.set_defaults(func=cmd_metrics)

    wd = sub.add_parser("worktree-diff")
    wd.add_argument("--repo-root", default=".")
    wd.add_argument("--base")
    wd.set_defaults(func=cmd_worktree_diff)

    st = sub.add_parser("setup")
    st.add_argument("--host", action="append", choices=tuple(_SKILL_HOSTS),
                    help="install only this host (repeatable); default: claude and codex")
    st.set_defaults(func=cmd_setup)

    oe = sub.add_parser("onboard-estimate")
    oe.add_argument("--repo", required=True)
    oe.set_defaults(func=cmd_onboard_estimate)

    od = sub.add_parser("onboard-detect")
    od.add_argument("--repo", required=True)
    od.set_defaults(func=cmd_onboard_detect)

    orp = sub.add_parser("onboard-report")
    # 必填, 无默认: derive_db 是 overwrite-style, 默认 "cowboy" 会清零已策展的 pack(见 conftest 事故记录)
    orp.add_argument("--domain-pack", required=True)
    orp.add_argument("--concepts-dir", required=True)
    orp.add_argument("--repo-root", action="append", default=[])
    orp.set_defaults(func=cmd_onboard_report)

    pc = sub.add_parser("plan-cost")
    pc.add_argument("--domain-pack", default="cowboy")
    pc.add_argument("--concepts-dir", required=True)
    pc.add_argument("--repo-root", action="append", default=[])
    pc.add_argument("--touches", required=True, help="JSON file: [{concept_id, op, importance?, est_scope?}]")
    pc.set_defaults(func=cmd_plan_cost)

    for name, fn in (("concept-tree", cmd_concept_tree), ("concept-list", cmd_concept_list)):
        cp = sub.add_parser(name)
        cp.add_argument("--domain-pack", default="cowboy")
        cp.add_argument("--concepts-dir", required=True)
        cp.add_argument("--repo-root", action="append", default=[],
                        help="repo=abs_path (可多次); anchor 校验用")
        cp.set_defaults(func=fn)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:  # 边界:把任何确定性失败转成 degraded 信号给 skill
        return _fail(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
