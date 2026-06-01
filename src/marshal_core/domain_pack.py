"""领域包契约 — 核心通过它取领域知识, 绝不硬编码项目专属内容。"""
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class InvariantDef:
    id: str
    domain: str
    spec_ref: str
    executor_kind: str
    location_repo: str
    location_path: str
    location_test: str
    severity: str = "mid"


@runtime_checkable
class DomainPack(Protocol):
    @property
    def id(self) -> str: ...

    def list_invariants(self, scope: dict) -> list[InvariantDef]: ...

    def classify(self, scope: dict) -> str:
        """返回 high|mid|low。"""
        ...
