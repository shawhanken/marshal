"""Validation helpers for review-run evidence manifests.

The manifest is deliberately small and reference-oriented: it records what was
run and whether it was available, without copying large logs into the knowledge
store.  In particular, an unavailable external scan is not the same as a scan
that completed with zero findings.
"""

from copy import deepcopy


REVIEW_RUN_STATUSES = {"open", "complete", "degraded"}
STEP_STATUSES = {"complete", "degraded", "unavailable", "not_run", "failed"}
COMMAND_STATUSES = {"pass", "fail", "degraded", "unavailable", "not_run"}
EXTERNAL_STATUSES = {"complete", "degraded", "unavailable", "not_run"}
REQUIRED_STEPS = {"closure", "scout", "prove", "invariant"}


def _require_string(value, field: str, *, non_empty: bool = False) -> None:
    if not isinstance(value, str) or (non_empty and not value.strip()):
        qualifier = "non-empty " if non_empty else ""
        raise ValueError(f"evidence {field} must be a {qualifier}string")


def _validate_string_list(value, field: str) -> None:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"evidence {field} must be a list of non-empty strings")


def _validate_status_map(value, field: str, allowed: set[str]) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"evidence {field} must be an object")
    for name, item in value.items():
        _require_string(name, f"{field} name", non_empty=True)
        if not isinstance(item, dict):
            raise ValueError(f"evidence {field}.{name} must be an object")
        status = item.get("status")
        if status not in allowed:
            raise ValueError(
                f"evidence {field}.{name}.status must be one of {sorted(allowed)}"
            )
        if status != "complete":
            _require_string(item.get("reason"), f"{field}.{name}.reason", non_empty=True)
        for optional in ("evidence_ref", "reason"):
            if optional in item and item[optional] is not None:
                _require_string(item[optional], f"{field}.{name}.{optional}")


def _validate_commands(commands, *, complete: bool = False) -> None:
    if not isinstance(commands, list):
        raise ValueError("evidence commands must be a list")
    for index, command in enumerate(commands):
        field = f"commands[{index}]"
        if not isinstance(command, dict):
            raise ValueError(f"evidence {field} must be an object")
        _require_string(command.get("name"), f"{field}.name", non_empty=True)
        if command.get("status") not in COMMAND_STATUSES:
            raise ValueError(
                f"evidence {field}.status must be one of {sorted(COMMAND_STATUSES)}"
            )
        if "argv" in command:
            _validate_string_list(command["argv"], f"{field}.argv")
        if complete:
            for required in ("argv", "exit_code", "log_ref"):
                if required not in command:
                    raise ValueError(f"evidence {field}.{required} is required for complete status")
            if not command["argv"]:
                raise ValueError(f"evidence {field}.argv must not be empty for complete status")
            _require_string(command["log_ref"], f"{field}.log_ref", non_empty=True)
        if "exit_code" in command and command["exit_code"] is not None:
            if (isinstance(command["exit_code"], bool)
                    or not isinstance(command["exit_code"], int)):
                raise ValueError(f"evidence {field}.exit_code must be an integer")
        if command.get("status") == "pass" and command.get("exit_code") not in (None, 0):
            raise ValueError(f"evidence {field} cannot be pass with a non-zero exit_code")
        if "log_ref" in command and command["log_ref"] is not None:
            _require_string(command["log_ref"], f"{field}.log_ref")
        tests = command.get("tests")
        if tests is not None:
            if not isinstance(tests, dict):
                raise ValueError(f"evidence {field}.tests must be an object")
            for name in ("passed", "failed", "skipped"):
                if name in tests and (
                    (not isinstance(tests[name], int)
                     or isinstance(tests[name], bool) or tests[name] < 0)
                ):
                    raise ValueError(
                        f"evidence {field}.tests.{name} must be a non-negative integer"
                    )
            if command.get("status") == "pass" and tests.get("failed", 0) != 0:
                raise ValueError(f"evidence {field} cannot be pass with failed tests")
    if complete and not commands:
        raise ValueError("evidence commands must contain at least one command for complete status")


def _validate_external_scans(scans) -> None:
    if not isinstance(scans, list):
        raise ValueError("evidence external_scans must be a list")
    if not scans:
        raise ValueError("evidence external_scans must contain at least one scan")
    names = set()
    for index, scan in enumerate(scans):
        field = f"external_scans[{index}]"
        if not isinstance(scan, dict):
            raise ValueError(f"evidence {field} must be an object")
        _require_string(scan.get("name"), f"{field}.name", non_empty=True)
        if scan["name"] in names:
            raise ValueError(f"evidence {field}.name is duplicated")
        names.add(scan["name"])
        status = scan.get("status")
        if status not in EXTERNAL_STATUSES:
            raise ValueError(
                f"evidence {field}.status must be one of {sorted(EXTERNAL_STATUSES)}"
            )
        findings = scan.get("findings")
        if status == "complete":
            if isinstance(findings, bool) or not isinstance(findings, int) or findings < 0:
                raise ValueError(
                    f"evidence {field}.findings is required as a non-negative integer "
                    "when the scan is complete"
                )
        elif findings is not None:
            raise ValueError(
                f"evidence {field}.findings must be omitted/null when status is {status}; "
                "unavailable is not zero findings"
            )
        if status != "complete":
            _require_string(scan.get("reason"), f"{field}.reason", non_empty=True)
        for optional in ("evidence_ref", "log_ref"):
            if optional in scan and scan[optional] is not None:
                _require_string(scan[optional], f"{field}.{optional}")


def validate_review_evidence(evidence: object, *, complete: bool = False) -> dict:
    """Validate and copy a review evidence manifest.

    Unknown top-level fields are retained so callers can add harmless metadata,
    while all structured fields used for gate decisions are type checked.
    """
    if not isinstance(evidence, dict):
        raise ValueError("review evidence must be a JSON object")
    out = deepcopy(evidence)
    for field in ("head_sha", "base_sha", "tree_sha", "platform", "worktree", "toolchain"):
        if field in out and out[field] is not None:
            _require_string(out[field], f"{field}")
    if "steps" in out:
        _validate_status_map(out["steps"], "steps", STEP_STATUSES)
    if "lenses" in out:
        lenses = out["lenses"]
        if not isinstance(lenses, dict):
            raise ValueError("evidence lenses must be an object")
        for field in ("expected", "returned", "missing"):
            if field in lenses:
                _validate_string_list(lenses[field], f"lenses.{field}")
                if len(lenses[field]) != len(set(lenses[field])):
                    raise ValueError(f"evidence lenses.{field} must not contain duplicates")
        returned = set(lenses.get("returned", []))
        missing = set(lenses.get("missing", []))
        if returned & missing:
            raise ValueError(
                "evidence lenses cannot list a lens as both returned and missing"
            )
    if "commands" in out:
        _validate_commands(out["commands"], complete=complete)
    if "external_scans" in out:
        _validate_external_scans(out["external_scans"])
    if "notes" in out:
        _validate_string_list(out["notes"], "notes")
    if complete:
        for field in ("head_sha", "base_sha", "tree_sha"):
            _require_string(out.get(field), field, non_empty=True)
        steps = out.get("steps")
        if not isinstance(steps, dict) or set(steps) != REQUIRED_STEPS:
            raise ValueError(
                "complete evidence steps must contain exactly "
                + ", ".join(sorted(REQUIRED_STEPS))
            )
        lenses = out.get("lenses")
        if not isinstance(lenses, dict):
            raise ValueError("complete evidence lenses are required")
        for field in ("expected", "returned", "missing"):
            if field not in lenses:
                raise ValueError(f"complete evidence lenses.{field} is required")
        expected = set(lenses["expected"])
        returned = set(lenses["returned"])
        missing = set(lenses["missing"])
        if not expected or returned | missing != expected or returned & missing:
            raise ValueError("complete evidence lenses must partition expected lenses")
    return out


def evidence_has_unresolved(evidence: dict) -> bool:
    """Return whether the manifest contains work that prevents a complete run."""
    for step in (evidence.get("steps") or {}).values():
        if step.get("status") != "complete":
            return True
    lenses = evidence.get("lenses") or {}
    if lenses.get("missing"):
        return True
    if (set(lenses.get("expected", [])) - set(lenses.get("returned", []))
            - set(lenses.get("missing", []))):
        return True
    if any(command.get("status") != "pass" for command in evidence.get("commands", [])):
        return True
    if any(scan.get("status") != "complete" for scan in evidence.get("external_scans", [])):
        return True
    return False
