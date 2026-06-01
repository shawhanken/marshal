"""Cowboy 领域包 (第一个领域包)。本切片只含经济守恒不变量 + 极简分级规则。"""
from dataclasses import dataclass
from marshal_core.domain_pack import InvariantDef

_HIGH_PREFIXES = (
    "execution/src/execution/engine",
    "execution/src/execution/transaction",
    "execution/src/execution/system_instruction",
    "execution/src/execution/basefee",
    "execution/src/runner/",
    "storage/src/speculative",
    "storage/src/process_block",
    "chain/",
)
_HIGH_SUBSTR = ("crypto", "_root")
_LOW_SUFFIXES = (".md",)
_LOW_SUBSTR = ("/tests/", "test_", "/scripts/", "tests.rs")
_SYS_ADDR_TOKENS = ("0x06", "0x09", "0x91", "0x92", "0x93", "0x94", "0x95")

REVIEW_DIMENSIONS = [
    {"name": "correctness", "prompt": "找出这个改动会怎样产生错误结果或破坏现有行为。"},
    {"name": "spec", "prompt": "实现是否偏离它所引用 CIP 的真实意图?指出语义漂移。"},
    {"name": "cross-repo", "prompt": "这个改动是否破坏跨 repo 契约(编码/类型序列化字节兼容)?"},
    {"name": "security", "prompt": "默认怀疑:有无越权、未校验输入、可被滥用的路径?"},
    {"name": "econ", "prompt": "gas/费用/escrow 守恒是否被破坏?burn+tip==fee?escrow 非负?"},
    {"name": "determinism", "prompt": "PVM 确定性:有无非确定来源、绕过 int guard、黑名单 import?"},
]

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

_CONTRACT_INVARIANTS = {
    "contract.tx_encoding_roundtrip": InvariantDef(
        id="contract.tx_encoding_roundtrip", domain="cross-repo", spec_ref="CIP-?",
        executor_kind="conformance-vector", location_repo="node",
        location_path="types/src/transaction.rs", location_test="tx_encoding_golden_vectors",
        severity="high",
        run_command=["cargo", "test", "-p", "cowboy-types", "tx_encoding_golden_vectors",
                     "--", "--exact"]),
    "contract.runner_types_serde": InvariantDef(
        id="contract.runner_types_serde", domain="cross-repo", spec_ref="C-1",
        executor_kind="conformance-vector", location_repo="node",
        location_path="runner/src/types.rs", location_test="runner_types_serde_compat",
        severity="high",
        run_command=["cargo", "test", "-p", "cowboy-node-runner", "runner_types_serde_compat",
                     "--", "--exact"]),
}


class CowboyPack:
    @property
    def id(self) -> str:
        return "cowboy"

    def list_invariants(self, scope: dict) -> list[InvariantDef]:
        out = []
        if scope.get("repo") == "node":
            out.extend(_ECON_INVARIANTS)
        seen = {i.id for i in out}
        for cid in self.contracts_hit(scope):
            for inv_id in _CONTRACT_BY_ID[cid].verify_invariants:
                inv = _CONTRACT_INVARIANTS.get(inv_id)
                if inv and inv.id not in seen:
                    out.append(inv)
                    seen.add(inv.id)
        return out

    def classify(self, scope: dict) -> str:
        return self.classify_detailed(scope)["tier"]

    def classify_detailed(self, scope: dict) -> dict:
        paths = scope.get("diff_paths", [])
        text = scope.get("diff_text", "")
        reasons = []

        contracts = self.contracts_hit(scope)
        for cid in contracts:
            reasons.append(f"cross_repo_contract:{cid}")

        if any(p.startswith(_HIGH_PREFIXES) for p in paths):
            reasons.append("high-risk path (execution/storage/chain consensus)")
        if any(s in p for p in paths for s in _HIGH_SUBSTR):
            reasons.append("crypto / *_root computation")
        if any(t in text for t in _SYS_ADDR_TOKENS):
            reasons.append("system actor address logic")
        if any(lbl in ("cip:new", "cip:interface-change")
               for lbl in scope.get("labels", [])):
            reasons.append("CIP new / interface change")

        if contracts or reasons:
            tier = "high"
        elif paths and all(p.endswith(_LOW_SUFFIXES) or any(s in p for s in _LOW_SUBSTR)
                           for p in paths):
            tier = "low"
        else:
            tier = "mid"
            reasons.append("default mid (ordinary actor / RPC handler)")

        return {"tier": tier, "reasons": reasons, "contracts_hit": contracts,
                "review_dimensions": [d["name"] for d in self.review_plan(tier)]}

    def review_plan(self, tier: str) -> list[dict]:
        n = {"high": 6, "mid": 3, "low": 1}.get(tier, 3)
        return REVIEW_DIMENSIONS[:n]

    def contracts_hit(self, scope: dict) -> list[str]:
        repo = scope.get("repo", "")
        paths = scope.get("diff_paths", [])
        hit = []
        for c in CONTRACTS:
            prefixes = tuple(c.trigger_paths.get(repo, []))
            if prefixes and any(p.startswith(prefixes) for p in paths):
                hit.append(c.id)
        return hit
