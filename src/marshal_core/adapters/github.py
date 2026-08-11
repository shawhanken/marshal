"""GitHub 适配器 — webhook 解析 + Check Run 回写。第一接入层, 领域无关。"""
from marshal_core.contracts import NormalizedEvent, GateDecision

_SHADOW_CONCLUSION = "neutral"


def parse_pull_request_event(payload: dict, diff_paths: list[str]) -> NormalizedEvent:
    """纯解析。diff_paths 由调用方从 GitHub files API 取得 — webhook payload
    本身不含改动文件列表, 任何藏在 payload 里的字段都不可信。"""
    pr = payload["pull_request"]
    return NormalizedEvent(
        kind="pr",
        repo=payload["repository"]["name"],
        change_ref=pr["head"]["sha"],
        diff_paths=diff_paths,
        labels=[lbl["name"] for lbl in pr.get("labels", [])],
        actor=pr.get("user", {}).get("login", ""),
    )


def build_check_run(decision: GateDecision, shadow: bool = True) -> dict:
    lines = [f"- {g['name']}: **{g['outcome']}** (ev: {g['evidence_ref']})"
             for g in decision.gates]
    summary = (f"verdict=`{decision.verdict}` tier=`{decision.tier}`\n\n"
               + "\n".join(lines)
               + ("\n\n_影子模式: 仅评论, 不阻断_" if shadow else ""))
    conclusion = _SHADOW_CONCLUSION if shadow else (
        "success" if decision.verdict == "pass" else "failure")
    return {
        "name": "marshal/invariants",
        "head_sha": decision.change_ref,
        "status": "completed",
        "conclusion": conclusion,
        "output": {"title": f"Marshal: {decision.verdict}", "summary": summary},
    }
