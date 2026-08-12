"""通用 CI reporter — 项目无关。问大脑要 run-specs, 执行 argv, 回报结果。
执行知识 (归属仓/执行器类型/argv) 全部来自大脑 /plan 响应; reporter 自身对
任何具体项目零知识。跑不了的检查如实进 not_run 并整体 degraded, 绝不假装跑过。"""
import base64
import json
import re
import subprocess
import sys
import time
import urllib.request

# Commands in the current domain packs use these cargo-test executor kinds.
# Unknown kinds fail closed instead of receiving the ordinary exit-code pass.
_CARGO_TEST_KINDS = {"proptest", "test", "conformance-vector"}
_KNOWN_EXECUTOR_KINDS = _CARGO_TEST_KINDS | {"command"}
_TIMEOUT_SEC = 600
_TOTAL_TIMEOUT_SEC = 3600


def _post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _tests_ran(output: str) -> bool:
    return sum(int(n) for n in re.findall(r"(\d+) passed", output)) > 0


def _decode_diff_paths(encoded: str) -> list[str]:
    if not encoded:
        return []
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as e:
        raise ValueError("diff paths are not valid base64") from e
    if not raw:
        return []
    if not raw.endswith(b"\0"):
        raise ValueError("diff paths payload is not NUL terminated")
    return [item.decode("utf-8", errors="surrogateescape")
            for item in raw[:-1].split(b"\0") if item]


def _parse_labels(raw: str) -> list[str]:
    if not raw or raw == "null":
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError("labels payload is not valid JSON") from e
    if not isinstance(value, list):
        raise ValueError("labels payload must be a JSON list")
    labels = []
    for item in value:
        name = item.get("name") if isinstance(item, dict) else item
        if not isinstance(name, str):
            raise ValueError("each label must have a string name")
        labels.append(name)
    return labels


def _validate_plan(plan: object) -> tuple[str, list[dict], list[dict]]:
    """Validate the untrusted plan while preserving a usable job id for reporting."""
    if not isinstance(plan, dict):
        raise ValueError("/plan response must be a JSON object")
    job_id = plan.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("/plan response has no non-empty job_id")
    raw = plan.get("invariants")
    if not isinstance(raw, list):
        return job_id, [], [{"invariant_id": "__plan__",
                             "reason": "/plan response invariants must be a list"}]
    valid = []
    not_run = []
    for index, inv in enumerate(raw):
        if not isinstance(inv, dict):
            not_run.append({"invariant_id": f"__plan_item_{index}",
                            "reason": "plan invariant must be an object"})
            continue
        inv_id = inv.get("invariant_id")
        if not isinstance(inv_id, str) or not inv_id:
            inv_id = f"__plan_item_{index}"
            not_run.append({"invariant_id": inv_id,
                            "reason": "plan invariant has no non-empty invariant_id"})
            continue
        argv = inv.get("run_command")
        if (not isinstance(argv, list) or not argv
                or any(not isinstance(arg, str) or not arg for arg in argv)):
            not_run.append({"invariant_id": inv_id,
                            "reason": "plan invariant has an invalid run_command"})
            continue
        valid.append(inv)
    return job_id, valid, not_run


def _post_results(brain_url: str, job_id: str, results: list[dict],
                  not_run: list[dict]) -> dict:
    status = "ok" if not not_run else "degraded"
    return _post(f"{brain_url}/results", {
        "job_id": job_id, "schema_version": "1", "kind": "invariant",
        "payload": {"results": results, "not_run": not_run},
        "cost": 0.0, "status": status})


def run(brain_url: str, repo: str, change_ref: str, diff_paths: list[str],
        labels: list[str] | None = None) -> int:
    if not brain_url or not repo or not change_ref:
        raise ValueError("brain_url, repo, and change_ref must be non-empty")
    brain_url = brain_url.rstrip("/")
    plan = _post(f"{brain_url}/plan", {"kind": "pr", "repo": repo,
                                       "change_ref": change_ref,
                                       "diff_paths": diff_paths,
                                       "labels": list(labels or [])})
    job_id, invariants, not_run = _validate_plan(plan)
    results = []
    deadline = time.monotonic() + _TOTAL_TIMEOUT_SEC
    for index, inv in enumerate(invariants):
        inv_id = inv["invariant_id"]
        location = inv.get("location_repo")
        if not isinstance(location, str) or not location:
            not_run.append({"invariant_id": inv_id,
                            "reason": "plan did not specify a non-empty location_repo"})
            continue
        if location != repo:
            not_run.append({"invariant_id": inv_id,
                            "reason": f"lives in repo {location!r}; this reporter "
                                      f"runs in {repo!r} and cannot execute it"})
            continue
        kind = inv.get("executor_kind")
        if kind not in _KNOWN_EXECUTOR_KINDS:
            not_run.append({"invariant_id": inv_id,
                            "reason": f"unsupported or missing executor_kind: {kind!r}"})
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            not_run.extend({"invariant_id": rest["invariant_id"],
                            "reason": f"reporter total timeout exceeded after "
                                      f"{_TOTAL_TIMEOUT_SEC}s"}
                           for rest in invariants[index:])
            break
        argv = inv["run_command"]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=min(_TIMEOUT_SEC, remaining))
        except subprocess.TimeoutExpired:
            not_run.append({"invariant_id": inv_id,
                            "reason": f"timed out after at most "
                                      f"{min(_TIMEOUT_SEC, remaining):.0f}s"})
            continue
        except Exception as e:
            not_run.append({"invariant_id": inv_id, "reason": str(e)})
            continue
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            results.append({"invariant_id": inv_id, "passed": False,
                            "detail": "command exited nonzero"})
        elif kind in _CARGO_TEST_KINDS and not _tests_ran(output):
            not_run.append({"invariant_id": inv_id,
                            "reason": "exit code 0 but no tests ran "
                                      "(test filter matched nothing?)"})
        else:
            results.append({"invariant_id": inv_id, "passed": True, "detail": ""})
    resp = _post_results(brain_url, job_id, results, not_run)
    print("marshal response:", json.dumps(resp))
    return 0


def _main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--brain-url", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--change-ref", required=True)
    p.add_argument("--diff-paths", default="")
    p.add_argument("--diff-paths-b64", default="")
    p.add_argument("--labels-json", default="[]")
    a = p.parse_args()
    try:
        paths = (_decode_diff_paths(a.diff_paths_b64) if a.diff_paths_b64
                 else [x for x in a.diff_paths.split(",") if x])
        labels = _parse_labels(a.labels_json)
        return run(a.brain_url, a.repo, a.change_ref, paths, labels)
    except (TypeError, ValueError, KeyError) as e:
        print(f"marshal reporter error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(_main())
