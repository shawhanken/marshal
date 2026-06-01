"""Cowboy 领域包 (第一个领域包)。本切片只含经济守恒不变量 + 极简分级规则。"""
from dataclasses import dataclass
from marshal_core.domain_pack import InvariantDef

_HIGH_PREFIXES = (
    "execution/src/execution/",
    "execution/src/runner/",
    "storage/src/speculative",
)

_ECON_INVARIANTS = [
    InvariantDef(id="econ.fee_conservation", domain="econ", spec_ref="CIP-3",
                 executor_kind="proptest", location_repo="node",
                 location_path="execution/src/econ_invariants.rs",
                 location_test="prop_fee_conservation", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-execution", "prop_fee_conservation", "--", "--exact"]),
    InvariantDef(id="econ.settlement_sum_100", domain="econ", spec_ref="CIP-2",
                 executor_kind="proptest", location_repo="node",
                 location_path="execution/src/econ_invariants.rs",
                 location_test="prop_settlement_sum_100", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-execution", "prop_settlement_sum_100", "--", "--exact"]),
    InvariantDef(id="econ.escrow_non_negative", domain="econ", spec_ref="CIP-2",
                 executor_kind="proptest", location_repo="node",
                 location_path="execution/src/econ_invariants.rs",
                 location_test="prop_escrow_non_negative", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-execution", "prop_escrow_non_negative", "--", "--exact"]),
]


@dataclass
class Contract:
    id: str
    repos: list[str]
    trigger_paths: dict[str, list[str]]   # repo -> 路径前缀列表
    verify_invariants: list[str]


CONTRACTS = [
    Contract(id="tx-encoding", repos=["wallet", "node"],
             trigger_paths={"wallet": ["src/tx/"],
                            "node": ["types/src/transaction"]},
             verify_invariants=["contract.tx_encoding_roundtrip"]),
    Contract(id="runner-types", repos=["runner", "node"],
             trigger_paths={"runner": ["crates/runner-common/src/types"],
                            "node": ["runner/src/types"]},
             verify_invariants=["contract.runner_types_serde"]),
]

_CONTRACT_BY_ID = {c.id: c for c in CONTRACTS}


class CowboyPack:
    @property
    def id(self) -> str:
        return "cowboy"

    def list_invariants(self, scope: dict) -> list[InvariantDef]:
        if scope.get("repo") != "node":
            return []
        return list(_ECON_INVARIANTS)

    def classify(self, scope: dict) -> str:
        paths = scope.get("diff_paths", [])
        if any(p.startswith(_HIGH_PREFIXES) for p in paths):
            return "high"
        return "mid"

    def contracts_hit(self, scope: dict) -> list[str]:
        repo = scope.get("repo", "")
        paths = scope.get("diff_paths", [])
        hit = []
        for c in CONTRACTS:
            prefixes = tuple(c.trigger_paths.get(repo, []))
            if prefixes and any(p.startswith(prefixes) for p in paths):
                hit.append(c.id)
        return hit
