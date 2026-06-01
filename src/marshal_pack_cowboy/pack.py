"""Cowboy 领域包 (第一个领域包)。本切片只含经济守恒不变量 + 极简分级规则。"""
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
