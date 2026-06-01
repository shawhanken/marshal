"""层间流动契约 — 跨 Python/Rust 的单一真相源 (经 model_json_schema 导出)。"""
from typing import Literal, Optional
from pydantic import BaseModel


class NormalizedEvent(BaseModel):
    kind: Literal["pr", "cip", "merge"]
    repo: str
    change_ref: str            # commit SHA / PR ref
    diff_paths: list[str] = []
    labels: list[str] = []
    actor: str = ""


class DispatchJob(BaseModel):
    job_id: str
    kind: Literal["invariant", "review", "impact"]
    target_repo: str
    change_ref: str
    params: dict = {}
    budget: Optional[int] = None


class StructuredResult(BaseModel):
    job_id: str
    schema_version: str = "1"
    kind: Literal["invariant", "review", "impact"]
    payload: dict
    cost: float = 0.0
    status: Literal["ok", "degraded", "error"] = "ok"


class GateDecision(BaseModel):
    change_ref: str
    tier: Literal["high", "mid", "low"]
    gates: list[dict] = []      # [{name, outcome, evidence_ref}]
    verdict: Literal["pass", "block", "needs_human"]


class PlanResponse(BaseModel):
    job_id: str
    invariants: list[dict] = []   # [{invariant_id, run_command}]
