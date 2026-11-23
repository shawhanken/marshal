"""③ ReviewOrch — quorum 聚合与召回率自适应校准 (机制, 领域无关).

多视角对抗 review 的去重 + 计票 + 收敛 + PR 输出预算校准。靠视角间的"分歧/一致"标问题,
防 AI 审 AI 的相关性盲区:孤立的无证据低危发现当噪声丢;达到 quorum 的发现确认;**任何高危
一律升 escalate**(终审归人,即便只有单视角提出)。同时解耦非触发型质量维度(可维护性/性能)
与崩溃触发型缺陷门控,避免正交维度聚类吞并、同源相邻独立缺陷误并与大 PR 硬上限截断。
不含任何领域语义。
"""
from collections import defaultdict
from pathlib import PurePosixPath
import re

_SEVERITY_RANK = {"low": 0, "mid": 1, "high": 2}
_PR_SEVERITY_WEIGHT = {
    "critical": 4, "high": 3, "mid": 2, "medium": 2,
    "low": 1, "info": 0, "trivial": 0,
}

NON_TRIGGER_DIMENSIONS = frozenset({
    "maintainability", "readability", "dead-code", "refactor",
    "performance", "complexity", "allocation",
})
TRIGGER_REFUTE_LENSES = frozenset({"reachability", "severity"})
_DETERMINISTIC_EVIDENCE = frozenset({"formal", "tla", "smt", "ast", "slicing"})

_HEADER_EXTS = (".h", ".hpp", ".hh", ".hxx", ".ipp", ".d.ts", ".pyi")
_IMPL_EXTS = (".c", ".cpp", ".cc", ".cxx", ".rs", ".go", ".java", ".ts", ".tsx", ".js", ".py")
_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec|testing)(/|$)|"
    r"(^|/)test_[^/]+$|_test\.[^/]+$|\.spec\.[^/]+$|\.test\.[^/]+$",
    re.IGNORECASE,
)
_SPECULATIVE_HEDGES_RE = re.compile(
    r"\b(nitpick|minor\s+style|optional\s+nit|purely\s+cosmetic|"
    r"might\s+theoretically|consider\s+adding\s+a\s+comment|typo\s+in\s+comment)\b",
    re.IGNORECASE,
)
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "this", "that", "from", "should",
    "consider", "when", "into", "line", "file", "code",
})

REFUTE_LENSES = [
    {"name": "reachability",
     "prompt": ("默认 refute, 除非能构造出真正走到该路径的输入: 上游守卫/前置校验是否"
                "已挡掉? 触发前提是否根本不成立?")},
    {"name": "stale-basis",
     "prompt": ("默认 refute: 该发现是否读了陈旧 checkout / 错的树 / 落后引用? 回被审"
                "改动本体的版本一手核对, 所述符号/行为是否真如描述。")},
    {"name": "intended-design",
     "prompt": ("默认 refute: 这是否是有意的设计裁定 / 既定语义而非缺陷? 回权威规格或"
                "设计记录核对, 别把有意取舍当漏洞。")},
    {"name": "severity",
     "prompt": ("默认 refute 其 severity: 即便路径成立, 影响是否被高估 (优雅失败 vs "
                "halt、单条请求 vs 全局)? 给不出对应影响的证据就降级或 refute。")},
    {"name": "already-guarded",
     "prompt": ("默认 refute: 框架或别处机制是否已隐含保证 (生命周期重置、既有计数器、"
                "原子性/回滚)? 回代码确认该不变量是否已成立。")},
]

QUALITY_LENSES = [
    {"name": "maintainability",
     "dimension": "maintainability",
     "prompt": ("检查本次改动是否引入不可达/残留死代码、接口或命名语义不一致、重复逻辑、"
                "吞掉上下文的错误处理或缺失默认实现。仅报有明确代码位置与具体修复锚点的问题。")},
    {"name": "performance",
     "dimension": "performance",
     "prompt": ("检查本次改动是否在循环或高频路径引入不变量重复计算、冗余拷贝与分配、"
                "N+1 外部调用或算法复杂度退化。给出受影响路径与复杂度/开销对比依据。")},
]


def _class_key(root_cause_class: str) -> str:
    head = (root_cause_class or "").split(":", 1)[0].strip()
    return head or "unclassified"


def _slug(s: str) -> str:
    out = []
    for ch in s.lower():
        out.append(ch if ch.isalnum() else "-")
    return "-".join(x for x in "".join(out).split("-") if x) or "x"


def _dim_family(dimension: str | None) -> str:
    d = (dimension or "").strip().lower()
    if d in ("maintainability", "readability", "dead-code", "refactor"):
        return "maintainability"
    if d in ("performance", "complexity", "allocation"):
        return "performance"
    return "behavioral"


def ratchet_lenses(escapes: list[dict], max_lenses: int = 8,
                   samples_per_class: int = 3) -> list[dict]:
    if not escapes:
        return []
    max_lenses = max(0, max_lenses)
    samples_per_class = max(0, samples_per_class)
    if max_lenses == 0:
        return []
    buckets: dict[str, dict] = {}
    for e in escapes:
        k = _class_key(e.get("root_cause_class", ""))
        b = buckets.setdefault(k, {"klass": k, "count": 0, "samples": []})
        b["count"] += 1
        desc = (e.get("description") or "").strip()
        if desc and desc not in b["samples"]:
            b["samples"].append(desc)
    ranked = sorted(buckets.values(), key=lambda b: (-b["count"], b["klass"]))
    lenses = []
    for b in ranked[:max_lenses]:
        samples = b["samples"][:samples_per_class]
        evidence = ("\n".join(f"  - {s}" for s in samples)
                    if samples else "  (无成文描述, 仅类别)")
        prompt = (
            f"默认怀疑: 本次改动是否**重新引入** `{b['klass']}` 类逃逸? "
            f"该类历史上咬过 {b['count']} 次, 先例根因:\n{evidence}\n"
            f"回被审改动的代码, 逐条核对上述根因模式是否在此复现 (相同的守恒/归属/"
            f"边界/授权/确定性破口)。命中就产出具体触发路径; 明确不适用就说明为何。")
        lenses.append({"name": f"ratchet:{_slug(b['klass'])}", "prompt": prompt,
                       "klass": b["klass"], "weight": b["count"]})
    seen: dict[str, int] = {}
    for lp in lenses:
        n = lp["name"]
        seen[n] = seen.get(n, 0) + 1
        if seen[n] > 1:
            lp["name"] = f"{n}-{seen[n]}"
    return lenses


def assign_refute_lenses(n: int, dimension: str | None = None) -> list[dict]:
    if n <= 0:
        return []
    dim = (dimension or "").strip().lower()
    pool = ([x for x in REFUTE_LENSES if x["name"] not in TRIGGER_REFUTE_LENSES]
            if dim in NON_TRIGGER_DIMENSIONS else REFUTE_LENSES)
    k = len(pool)
    return [dict(pool[i % k]) for i in range(n)]


def _line_of(f: dict) -> int:
    v = f.get("line") if f.get("line") is not None else f.get("start_line")
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0


def _has_loc(f: dict) -> bool:
    return (f.get("file") not in (None, "", "?")
            and (f.get("line") not in (None, "") or f.get("start_line") not in (None, "")))


def _is_verified_finding(f: dict) -> bool:
    if f.get("verified") or f.get("invariant_id"):
        return True
    kind = str(f.get("evidence_kind") or "").strip().lower()
    return kind in _DETERMINISTIC_EVIDENCE


def _cluster_keys(findings: list[dict], proximity: int) -> list[str]:
    proximity = max(0, proximity)
    keys: list = [None] * len(findings)
    buckets: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for i, f in enumerate(findings):
        if f.get("key"):
            keys[i] = f["key"]
        elif _has_loc(f):
            fam = _dim_family(f.get("dimension"))
            sym = str(f.get("symbol") or "").strip()
            buckets[(str(f["file"]), fam, sym)].append(i)
        else:
            keys[i] = f"~loc?:{i}"

    for (file, fam, sym), idxs in buckets.items():
        idxs.sort(key=lambda i: _line_of(findings[i]))
        prefix = file if fam == "behavioral" else f"{fam}:{file}"
        if sym:
            prefix = f"{prefix}@{sym}"

        cluster: list[int] = []
        source_lines: dict[str, set[int]] = defaultdict(set)
        lo = 0
        seq = 0

        def _flush(items: list[int], start_ln: int, sub_seq: int) -> None:
            end_ln = _line_of(findings[items[-1]])
            tag = f"{prefix}#{sub_seq}" if sub_seq > 0 else prefix
            k = f"~prox:{tag}:{start_ln}~{end_ln}"
            for j in items:
                keys[j] = k

        for i in idxs:
            ln = _line_of(findings[i])
            src = str(findings[i].get("source") or "").strip()
            same_source_diff_line = bool(
                cluster and src and src in source_lines and ln not in source_lines[src]
            )
            if cluster and (ln - lo > proximity or same_source_diff_line):
                _flush(cluster, lo, seq)
                if same_source_diff_line:
                    seq += 1
                cluster = []
                source_lines.clear()
            if not cluster:
                lo = ln
            cluster.append(i)
            if src:
                source_lines[src].add(ln)
        if cluster:
            _flush(cluster, lo, seq)
    return keys


def aggregate_review(findings: list[dict], quorum: int = 2,
                     proximity: int = 10) -> dict:
    key_for = _cluster_keys(findings, proximity)
    groups: dict[str, dict] = {}
    for i, f in enumerate(findings):
        k = key_for[i]
        sev = str(f.get("severity") or "low").strip().lower()
        if sev not in _SEVERITY_RANK:
            sev = "mid"
        g = groups.setdefault(k, {
            "key": k, "severity": "low", "sources": set(),
            "dimensions": set(), "titles": [], "count": 0,
            "file": f.get("file") or "", "line": _line_of(f), "verified": False,
        })
        if _SEVERITY_RANK[sev] > _SEVERITY_RANK[g["severity"]]:
            g["severity"] = sev
        if f.get("source"):
            g["sources"].add(f["source"])
        if f.get("dimension"):
            g["dimensions"].add(f["dimension"])
        if _is_verified_finding(f):
            g["verified"] = True
        if not g["file"] and f.get("file"):
            g["file"] = f["file"]
        if not g["line"] and _line_of(f):
            g["line"] = _line_of(f)
        g["count"] += 1
        if f.get("title"):
            g["titles"].append(f["title"])

    out_groups = []
    for g in groups.values():
        support = len(g["sources"]) or 1
        if g["severity"] == "high":
            status = "escalate"
        elif support >= quorum:
            status = "confirmed"
        elif g["severity"] == "mid" or g["verified"]:
            status = "advisory"
        else:
            status = "weak"
        out_groups.append({
            "key": g["key"], "severity": g["severity"],
            "support": support, "sources": sorted(g["sources"]),
            "dimensions": sorted(g["dimensions"]),
            "titles": g["titles"], "status": status,
            "file": g["file"], "line": g["line"], "verified": g["verified"],
        })

    out_groups.sort(key=lambda x: (-_SEVERITY_RANK[x["severity"]], -x["support"]))
    escalate = [g for g in out_groups if g["status"] == "escalate"]
    confirmed = [g for g in out_groups if g["status"] == "confirmed"]
    advisory = [g for g in out_groups if g["status"] == "advisory"]
    dropped = [g for g in out_groups if g["status"] == "weak"]
    verdict = "escalate" if escalate else "pass"
    return {"groups": out_groups, "escalate": escalate,
            "confirmed": confirmed, "advisory": advisory, "dropped": dropped,
            "review_verdict": verdict}


def verify_findings(items: list[dict], exempt_non_trigger: bool = True) -> dict:
    survived, killed, unverified = [], [], []
    for it in items:
        raw_votes = list(it.get("votes", []) or [])
        dim = str(it.get("dimension") or "").strip().lower()
        votes = raw_votes
        if exempt_non_trigger and dim in NON_TRIGGER_DIMENSIONS and raw_votes:
            factual = [
                v for v in raw_votes
                if str(v.get("lens") or "").strip().lower() not in TRIGGER_REFUTE_LENSES
            ]
            if factual:
                votes = factual
        total = len(votes)
        refutes = sum(1 for v in votes if v.get("refuted"))
        upholds = total - refutes
        row = {"key": it.get("key"), "severity": it.get("severity", "low"),
               "upholds": upholds, "refutes": refutes, "total": total}
        if dim:
            row["dimension"] = dim
        if total == 0:
            unverified.append(row)
        elif upholds * 2 > total:
            survived.append(row)
        else:
            killed.append(row)
    verdict = "escalate" if any(r["severity"] == "high" for r in survived) else "pass"
    return {"survived": survived, "killed": killed, "unverified": unverified,
            "verdict": verdict}


def is_test_path(path: str) -> bool:
    norm = (path or "").strip().lstrip("./").replace("\\", "/")
    return bool(_TEST_PATH_RE.search(norm))


def adaptive_pr_cap(changed_lines: int = 0,
                    changed_files: list[str] | set[str] | None = None,
                    base_cap: int = 10, max_cap: int = 28) -> int:
    base_cap = max(1, base_cap)
    max_cap = max(base_cap, max_cap)
    changed_lines = max(0, int(changed_lines or 0))
    non_test_files = (
        sum(1 for p in changed_files if not is_test_path(p))
        if changed_files else 0
    )
    line_scaled = base_cap + max(0, changed_lines - 200) // 70
    file_scaled = base_cap + max(0, non_test_files - 3) * 2
    return min(max_cap, max(base_cap, line_scaled, file_scaled))


def adaptive_closure_budget(changed_lines: int = 0, file_count: int = 1) -> dict:
    changed_lines = max(0, int(changed_lines or 0))
    file_count = max(1, int(file_count or 1))
    closure_lines = min(4500, max(1500, 1500 + max(0, changed_lines - 300) * 2))
    per_lens_cap = min(10, max(6, 6 + max(0, changed_lines - 400) // 250))
    global_cap = min(42, max(18, 18 + max(0, changed_lines - 300) // 80 + max(0, file_count - 4)))
    shards = max(1, (changed_lines + 799) // 800)
    return {
        "closure_lines": closure_lines,
        "per_lens_cap": per_lens_cap,
        "global_cap": global_cap,
        "recommended_shards": shards,
    }


def _strip_known_ext(path: str) -> tuple[str, str]:
    for ext in (*_HEADER_EXTS, *_IMPL_EXTS):
        if path.endswith(ext):
            return path[:-len(ext)], ext
    p = PurePosixPath(path)
    return (path[:-len(p.suffix)] if p.suffix else path), p.suffix


def resolve_changed_anchor(path: str,
                           changed_files: set[str] | list[str] | None) -> str | None:
    norm = (path or "").strip().lstrip("./").replace("\\", "/")
    if not norm:
        return None
    if changed_files is None:
        return norm
    changed = {p.strip().lstrip("./").replace("\\", "/") for p in changed_files if p}
    if norm in changed:
        return norm

    suffix_hits = [c for c in changed if c.endswith("/" + norm) or norm.endswith("/" + c)]
    if len(suffix_hits) == 1:
        return suffix_hits[0]

    stem, _ = _strip_known_ext(norm)
    base_name = PurePosixPath(stem).name
    same_dir_peers = []
    basename_peers = []
    for cand in sorted(changed):
        c_stem, _ = _strip_known_ext(cand)
        if c_stem == stem:
            same_dir_peers.append(cand)
        elif PurePosixPath(c_stem).name == base_name and base_name not in ("mod", "index", "lib", "__init__"):
            basename_peers.append(cand)
    if len(same_dir_peers) == 1:
        return same_dir_peers[0]
    if len(basename_peers) == 1:
        return basename_peers[0]
    return None


def _tokens(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text.lower())
        if w not in _STOPWORDS
    }


def _item_dim(it: dict) -> str:
    if it.get("dimension"):
        return str(it["dimension"])
    if it.get("category"):
        return str(it["category"])
    dims = it.get("dimensions")
    if isinstance(dims, (list, tuple, set)) and dims:
        return str(next(iter(dims)))
    return "correctness"


def _item_text(it: dict) -> str:
    titles = it.get("titles")
    joined_titles = " ".join(str(x) for x in titles) if isinstance(titles, (list, tuple)) else ""
    return f"{it.get('title', '')} {joined_titles} {it.get('summary', '')} {it.get('content', '')}".strip()


def _semantic_overlap(a: dict, b: dict) -> float:
    dim_a = _dim_family(_item_dim(a))
    dim_b = _dim_family(_item_dim(b))
    if dim_a != dim_b:
        return 0.0
    ta = _tokens(_item_text(a))
    tb = _tokens(_item_text(b))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    score = max(inter / len(ta | tb), 0.78 * (inter / min(len(ta), len(tb))))
    la, lb = _line_of(a), _line_of(b)
    if la and lb and abs(la - lb) <= 3:
        score += 0.15
    return score


def select_pr_findings(
    candidates: list[dict],
    *,
    changed_files: set[str] | list[str] | None = None,
    changed_lines: int = 0,
    base_cap: int = 10,
    max_cap: int = 28,
    dedup_threshold: float = 0.52,
) -> list[dict]:
    norm_changed = (
        {p.strip().lstrip("./").replace("\\", "/") for p in changed_files if p}
        if changed_files is not None else None
    )
    has_non_test_changes = bool(norm_changed and any(not is_test_path(p) for p in norm_changed))

    filtered: list[dict] = []
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        raw_path = str(raw.get("file") or raw.get("path") or "")
        anchored = resolve_changed_anchor(raw_path, norm_changed)
        if not anchored:
            continue

        sev = str(raw.get("severity") or "mid").strip().lower()
        dim = _item_dim(raw).strip().lower()
        verified = _is_verified_finding(raw)
        confirmed = verified or raw.get("status") in ("escalate", "confirmed") or int(raw.get("support") or 1) >= 2
        text_blob = _item_text(raw)
        if not text_blob:
            continue

        if has_non_test_changes and is_test_path(anchored):
            is_defect = _dim_family(dim) == "behavioral" and dim not in ("style", "formatting")
            if not (is_defect and (confirmed or sev in ("critical", "high"))):
                continue

        if not verified and sev in ("trivial", "info"):
            continue
        if not verified and _SPECULATIVE_HEDGES_RE.search(text_blob) and sev in ("low", "trivial", "info"):
            continue

        item = {**raw, "file": anchored}
        if anchored != raw_path.strip().lstrip("./").replace("\\", "/"):
            item["anchored_from"] = raw_path
        filtered.append(item)

    def _priority(it: dict) -> tuple[int, int, int, int]:
        ver = 1 if _is_verified_finding(it) else 0
        conf = 1 if (it.get("status") in ("escalate", "confirmed") or int(it.get("support") or 1) >= 2) else 0
        sev_w = _PR_SEVERITY_WEIGHT.get(str(it.get("severity") or "mid").lower(), 2)
        non_test = 0 if is_test_path(it["file"]) else 1
        return (ver, conf, sev_w, non_test)

    filtered.sort(key=_priority, reverse=True)

    kept_by_file: dict[str, list[dict]] = {}
    for item in filtered:
        existing = kept_by_file.setdefault(item["file"], [])
        if any(_semantic_overlap(item, prev) >= dedup_threshold for prev in existing):
            continue
        existing.append(item)

    flattened = [it for items in kept_by_file.values() for it in items]
    flattened.sort(key=_priority, reverse=True)

    cap = adaptive_pr_cap(
        changed_lines=changed_lines,
        changed_files=norm_changed,
        base_cap=base_cap,
        max_cap=max_cap,
    )
    verified_hits = [it for it in flattened if _is_verified_finding(it)]
    regular_hits = [it for it in flattened if not _is_verified_finding(it)]
    remaining = max(0, cap - len(verified_hits))
    return verified_hits + regular_hits[:remaining]
