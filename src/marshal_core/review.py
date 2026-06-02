"""③ ReviewOrch — quorum 聚合 (机制, 领域无关).

多视角对抗 review 的去重 + 计票 + 收敛。靠视角间的"分歧/一致"标问题,防 AI 审 AI
的相关性盲区:孤立的低危发现当噪声丢;达到 quorum 的发现确认;**任何高危一律升
needs_human**(终审归人,即便只有单视角提出)。不含任何领域语义。
"""
_SEVERITY_RANK = {"low": 0, "mid": 1, "high": 2}


def _key(f: dict) -> str:
    if f.get("key"):
        return f["key"]
    return f"{f.get('file', '?')}:{f.get('line', '?')}:{f.get('dimension', '?')}"


def aggregate_review(findings: list[dict], quorum: int = 2) -> dict:
    """把多视角发现聚合成 review 结论。

    每条 finding: {key? | file,line,dimension, severity(low|mid|high), source, title}.
    同 key 的归一组;support = 不同 `source` 数(同视角重复不加分)。
    组状态: 含高危 → needs_human;否则 support>=quorum → confirmed;否则 weak(丢弃)。
    review_verdict: 有任一高危组 → needs_human;否则 pass(confirmed 的中/低危为建议态)。
    """
    groups: dict[str, dict] = {}
    for f in findings:
        k = _key(f)
        sev = f.get("severity", "low")
        g = groups.setdefault(k, {"key": k, "severity": "low", "sources": set(),
                                  "titles": [], "count": 0})
        if _SEVERITY_RANK.get(sev, 0) > _SEVERITY_RANK[g["severity"]]:
            g["severity"] = sev
        if f.get("source"):
            g["sources"].add(f["source"])
        g["count"] += 1
        if f.get("title"):
            g["titles"].append(f["title"])

    out_groups = []
    for g in groups.values():
        support = len(g["sources"]) or g["count"]
        if g["severity"] == "high":
            status = "needs_human"
        elif support >= quorum:
            status = "confirmed"
        else:
            status = "weak"
        out_groups.append({"key": g["key"], "severity": g["severity"],
                           "support": support, "sources": sorted(g["sources"]),
                           "titles": g["titles"], "status": status})

    # 稳定排序: 高危在前, 再按 support 降序
    out_groups.sort(key=lambda x: (-_SEVERITY_RANK[x["severity"]], -x["support"]))
    needs_human = [g for g in out_groups if g["status"] == "needs_human"]
    confirmed = [g for g in out_groups if g["status"] == "confirmed"]
    dropped = [g for g in out_groups if g["status"] == "weak"]
    verdict = "needs_human" if needs_human else "pass"
    return {"groups": out_groups, "needs_human": needs_human,
            "confirmed": confirmed, "dropped": dropped, "review_verdict": verdict}


def verify_findings(items: list[dict]) -> dict:
    """③ 对抗式验证二段: 对每条发现的 N 个 skeptic 投票裁决 (default-to-refute)。

    每条 item: {key, severity, votes:[{refuted: bool, reason?}]}. skeptic 默认 refute,
    只有确凿证明发现为真才 uphold。**仅当严格多数 uphold 才存活**(平票/多数 refute →
    杀,把似是而非的误报砍掉);无投票 → unverified(degraded,保留待人看)。
    verdict: 有存活的高危 → needs_human;否则 pass。
    """
    survived, killed, unverified = [], [], []
    for it in items:
        votes = it.get("votes", []) or []
        total = len(votes)
        refutes = sum(1 for v in votes if v.get("refuted"))
        upholds = total - refutes
        row = {"key": it.get("key"), "severity": it.get("severity", "low"),
               "upholds": upholds, "refutes": refutes, "total": total}
        if total == 0:
            unverified.append(row)
        elif upholds * 2 > total:          # 严格多数 uphold 才存活
            survived.append(row)
        else:                              # 平票或多数 refute → 杀
            killed.append(row)
    verdict = "needs_human" if any(r["severity"] == "high" for r in survived) else "pass"
    return {"survived": survived, "killed": killed, "unverified": unverified,
            "verdict": verdict}
