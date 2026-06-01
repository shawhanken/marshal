"""通用 CI reporter — 项目无关。问大脑要 run-specs, 执行 argv, 回报结果。
对 cargo/rust/任何具体项目零知识: 可执行命令全部来自大脑 /plan 响应。"""
import json
import subprocess
import sys
import urllib.request


def _post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def run(brain_url: str, repo: str, change_ref: str, diff_paths: list[str]) -> int:
    brain_url = brain_url.rstrip("/")
    plan = _post(f"{brain_url}/plan", {"kind": "pr", "repo": repo,
                                       "change_ref": change_ref,
                                       "diff_paths": diff_paths})
    job_id = plan["job_id"]
    results = []
    status = "ok"
    for inv in plan["invariants"]:
        argv = inv["run_command"]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True)
            passed = proc.returncode == 0
            detail = "" if passed else "command exited nonzero"
        except Exception as e:
            passed, status, detail = False, "degraded", str(e)
        results.append({"invariant_id": inv["invariant_id"],
                        "passed": passed, "detail": detail})
    resp = _post(f"{brain_url}/results", {
        "job_id": job_id, "schema_version": "1", "kind": "invariant",
        "payload": {"results": results}, "cost": 0.0, "status": status})
    print("marshal response:", json.dumps(resp))
    return 0


def _main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--brain-url", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--change-ref", required=True)
    p.add_argument("--diff-paths", default="")
    a = p.parse_args()
    paths = [x for x in a.diff_paths.split(",") if x]
    return run(a.brain_url, a.repo, a.change_ref, paths)


if __name__ == "__main__":
    sys.exit(_main())
