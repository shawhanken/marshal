"""通用 CI reporter — 项目无关。问大脑要 run-specs, 执行 argv, 回报结果。
执行知识 (归属仓/执行器类型/argv) 全部来自大脑 /plan 响应; reporter 自身对
任何具体项目零知识。跑不了的检查如实进 not_run 并整体 degraded, 绝不假装跑过。"""
import json
import re
import subprocess
import sys
import urllib.request

# executor kinds whose commands are `cargo test` invocations: exit code 0 alone
# does not prove anything ran (a non-matching filter still exits 0 with
# "running 0 tests"), so require at least one reported "N passed".
_CARGO_TEST_KINDS = {"proptest", "test", "conformance-vector"}
_TIMEOUT_SEC = 600


def _post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _tests_ran(output: str) -> bool:
    return sum(int(n) for n in re.findall(r"(\d+) passed", output)) > 0


def run(brain_url: str, repo: str, change_ref: str, diff_paths: list[str]) -> int:
    brain_url = brain_url.rstrip("/")
    plan = _post(f"{brain_url}/plan", {"kind": "pr", "repo": repo,
                                       "change_ref": change_ref,
                                       "diff_paths": diff_paths})
    job_id = plan["job_id"]
    results = []
    not_run = []
    for inv in plan["invariants"]:
        inv_id = inv["invariant_id"]
        location = inv.get("location_repo")
        if location is None:
            not_run.append({"invariant_id": inv_id,
                            "reason": "plan did not specify location_repo"})
            continue
        if location != repo:
            not_run.append({"invariant_id": inv_id,
                            "reason": f"lives in repo {location!r}; this reporter "
                                      f"runs in {repo!r} and cannot execute it"})
            continue
        argv = inv["run_command"]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            not_run.append({"invariant_id": inv_id,
                            "reason": f"timed out after {_TIMEOUT_SEC}s"})
            continue
        except Exception as e:
            not_run.append({"invariant_id": inv_id, "reason": str(e)})
            continue
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            results.append({"invariant_id": inv_id, "passed": False,
                            "detail": "command exited nonzero"})
        elif inv.get("executor_kind") in _CARGO_TEST_KINDS and not _tests_ran(output):
            not_run.append({"invariant_id": inv_id,
                            "reason": "exit code 0 but no tests ran "
                                      "(test filter matched nothing?)"})
        else:
            results.append({"invariant_id": inv_id, "passed": True, "detail": ""})
    status = "ok" if not not_run else "degraded"
    resp = _post(f"{brain_url}/results", {
        "job_id": job_id, "schema_version": "1", "kind": "invariant",
        "payload": {"results": results, "not_run": not_run},
        "cost": 0.0, "status": status})
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
