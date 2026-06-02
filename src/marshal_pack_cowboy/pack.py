"""Cowboy 领域包 (第一个领域包)。本切片只含经济守恒不变量 + 极简分级规则。"""
import re
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
                 location_test="econ_invariants::prop_fee_conservation", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-execution", "econ_invariants::prop_fee_conservation", "--", "--exact"]),
    InvariantDef(id="econ.settlement_sum_100", domain="econ", spec_ref="CIP-2",
                 executor_kind="proptest", location_repo="node",
                 location_path="execution/src/econ_invariants.rs",
                 location_test="econ_invariants::prop_settlement_sum_100", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-execution", "econ_invariants::prop_settlement_sum_100", "--", "--exact"]),
    InvariantDef(id="econ.escrow_non_negative", domain="econ", spec_ref="CIP-2",
                 executor_kind="proptest", location_repo="node",
                 location_path="execution/src/econ_invariants.rs",
                 location_test="econ_invariants::prop_escrow_non_negative", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-execution", "econ_invariants::prop_escrow_non_negative", "--", "--exact"]),
]


@dataclass
class Contract:
    id: str
    repos: list[str]
    trigger_paths: dict[str, list[str]]   # repo -> 路径前缀列表
    verify_invariants: list[str]


CONTRACTS = [
    Contract(id="tx-encoding", repos=["wallet", "node"],
             trigger_paths={"wallet": ["src/lib/cbor", "src/lib/codec"],
                            "node": ["types/src/execution"]},
             verify_invariants=["contract.tx_encoding_roundtrip"]),
    Contract(id="runner-types", repos=["runner", "node"],
             trigger_paths={"runner": ["crates/runner-common/src/types"],
                            "node": ["runner/src/types"]},
             verify_invariants=["contract.runner_types_serde"]),
]

_CONTRACT_BY_ID = {c.id: c for c in CONTRACTS}

_CONTRACT_INVARIANTS = {
    "contract.tx_encoding_roundtrip": InvariantDef(
        id="contract.tx_encoding_roundtrip", domain="cross-repo", spec_ref="WP",
        executor_kind="conformance-vector", location_repo="node",
        location_path="types/src/execution.rs", location_test="tx_encoding_golden_vectors",
        severity="high",
        run_command=["cargo", "test", "-p", "cowboy-types", "tx_encoding_golden_vectors",
                     "--", "--exact"]),
    "contract.runner_types_serde": InvariantDef(
        id="contract.runner_types_serde", domain="cross-repo", spec_ref="CIP-2",
        executor_kind="conformance-vector", location_repo="node",
        location_path="runner/src/types.rs", location_test="runner_types_serde_compat",
        severity="high",
        run_command=["cargo", "test", "-p", "cowboy-node-runner", "runner_types_serde_compat",
                     "--", "--exact"]),
}

# State/consensus invariant family — harvested from the knowledge core into the
# version-controlled pack (架构 §4.6(4) DB⇄包晋升回路). Grown via the Marshal
# ratchet (escape esc-20260601-01) while reviewing the CIP-4 state-sync PR.
# Surfaced when a node change touches the state/consensus surface (_STATE_PREFIXES).
# NOTE: these live in a module-nested test, so run_command uses a substring
# filter (NOT `-- --exact`, which won't match `state_invariants::tests::…`).
_STATE_PREFIXES = ("storage/", "chain/")

_STATE_INVARIANTS = [
    InvariantDef(id="state.root_consistent_propose_verify_report", domain="state-consensus",
                 spec_ref="CIP-4", executor_kind="test", location_repo="node",
                 location_path="storage/src/state_invariants.rs",
                 location_test="prop_merkle_root_consistent_across_phases", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-storage",
                              "prop_merkle_root_consistent_across_phases"]),
    InvariantDef(id="state.speculative_rollback_equivalent", domain="state-consensus",
                 spec_ref="CIP-4", executor_kind="test", location_repo="node",
                 location_path="storage/src/state_invariants.rs",
                 location_test="prop_speculative_rollback_equivalent", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-storage",
                              "prop_speculative_rollback_equivalent"]),
    InvariantDef(id="state.root_reflects_committed_set", domain="state-consensus",
                 spec_ref="CIP-4", executor_kind="test", location_repo="node",
                 location_path="storage/src/state_invariants.rs",
                 location_test="prop_root_reflects_committed_set", severity="high",
                 run_command=["cargo", "test", "-p", "cowboy-storage",
                              "prop_root_reflects_committed_set"]),
]


# CBSS IBE crypto invariant family — harvested from the knowledge core into the
# version-controlled pack (架构 §4.6(4) DB⇄包晋升回路). Grown via the Marshal
# ratchet (escape cbss-crypto-no-invariant) while reviewing the cowboy-crypto SDK
# packaging PR (node #470): the cbss-crypto crate had zero invariant coverage —
# only a single fixed-vector golden test. Surfaced when a change touches the
# crypto crate or its Python bindings (_CRYPTO_PREFIXES).
# NOTE: the proptest lives in `mod tests`, so location_test is fully-qualified
# (`tests::…`) and run_command keeps `-- --exact` (a bare name would filter to 0).
_CRYPTO_PREFIXES = ("cbss-crypto/", "cowboy-py/")

_CRYPTO_INVARIANTS = [
    InvariantDef(id="crypto.cbss_ibe_roundtrip", domain="crypto", spec_ref="CIP-24",
                 executor_kind="proptest", location_repo="node",
                 location_path="cbss-crypto/src/lib.rs",
                 location_test="tests::prop_cbss_ibe_roundtrip", severity="high",
                 run_command=["cargo", "test", "-p", "cbss-crypto",
                              "tests::prop_cbss_ibe_roundtrip", "--", "--exact"]),
]


# 安全信任边 / 危险点 (架构: 否定性·对抗性属性,不可往返化)。
# 教训源 = almanax 在 node #470 标出的 Critical:`cbss_encrypt_secret` 把 wrap key
# 派生成 `pairing(H1(aad), mpk_g2)`,无 per-message 随机数,而 `mpk_g2` 经 RPC
# (`/cbss/account-release-key/...`) 公开 —— 任何观察者可复算 wrap key 解密,机密性破裂。
# 关键认知:机密性 / IND-CPA 是**否定性属性**("除持密钥者外无人能恢复明文")。
# 功能往返不变量 `crypto.cbss_ibe_roundtrip`(decrypt(encrypt(m))==m)在脆弱构造上
# 照样为绿,结构上表达不了它。所以棘轮**不能**用一条 roundtrip proptest 来"补"这类洞;
# 它只能作为 review lens 的危险点暴露 (invariant_able=False)。同时它需要跨 crate 的
# 信任模型知识 (mpk 经 RPC 公开),不在 diff 内 —— 故显式建模成一条 trust 边。
@dataclass
class SecurityHazard:
    id: str
    when_paths: tuple[str, ...]      # 命中这些 diff 前缀即触发
    title: str
    prompt: str                       # 喂给 security review lens 的对抗式提示
    invariant_able: bool              # False => 不可固化为往返不变量,只能 review 裁定


SECURITY_HAZARDS = [
    SecurityHazard(
        id="cbss-mpk-rpc-exposure",
        when_paths=("cbss-crypto/", "cowboy-py/", "cbss/"),
        title="confidentiality of key derivation against a publicly exposed master key",
        prompt=("默认怀疑机密性而非功能性。CBSS master public key (mpk_g2) 经 RPC 公开 "
                "(/cbss/account-release-key/...)。检查任何 DEK / wrap-key 派生是否只用了"
                "公开量且无 per-message 随机数 —— 若是,任何观察者可复算密钥解密,机密性"
                "破裂 (IND-CPA 失败)。这是否定性属性:roundtrip 不变量无法表达,必须在此 "
                "review 中裁定 (参 node #470 almanax Critical)。"),
        invariant_able=False),
]


# 分层规格体系 (架构 §4.5): 白皮书=宪法, CIP=修正案. 源在 cowboy 仓库.
# 权威源 (用户确认): https://github.com/cowboyinc/cowboy/tree/main/docs/{cips,whitepaper}
SPEC_LAYERS = [
    {"id": "whitepaper", "role": "constitution", "authority": "root",
     "repo": "cowboy", "source": "docs/whitepaper",
     "main": "cowboy-technical-whitepaper.md"},
    {"id": "cip", "role": "amendment", "authority": "amends-constitution",
     "repo": "cowboy", "source": "docs/cips"},
]
PRECEDENCE_NORMATIVE = ["whitepaper", "cip"]            # 应然: CIP 在触及处覆盖白皮书
PRECEDENCE_DESCRIPTIVE = ["whitepaper", "cip", "code"]  # 实然: 代码为锚

_CIP_RE = re.compile(r"^CIP-(\d+)$")

# RFC2119 normative keywords, longest-first so "MUST NOT" wins over "MUST".
_RFC2119 = [
    ("MUST NOT", "must"), ("SHALL NOT", "must"), ("MUST", "must"),
    ("SHALL", "must"), ("REQUIRED", "must"),
    ("SHOULD NOT", "should"), ("SHOULD", "should"), ("RECOMMENDED", "should"),
    ("MAY", "may"), ("OPTIONAL", "may"),
]
_RFC2119_RE = [(kw, level, re.compile(r"\b" + kw.replace(" ", r"\s+") + r"\b"))
               for kw, level in _RFC2119]


class CowboyPack:
    @property
    def id(self) -> str:
        return "cowboy"

    def spec_layers(self) -> list[dict]:
        """分层规格体系声明 (供 ⑤/③ 解析规格层与定位源)。"""
        return [dict(layer) for layer in SPEC_LAYERS]

    def resolve_spec_ref(self, spec_ref: str) -> dict | None:
        """把一个 spec_ref 标签解析到其正文源位置 (repo + path_glob)。

        `CIP-<n>` → cowboy 仓库 docs/cips/cip-<n>-*.md;`WP`/`WHITEPAPER` →
        技术白皮书主文件。非规格标签 (如 C-1 / M-B / CIP-?) 返回 None —— 它们
        不是 CIP/白皮书条款,没有可读正文源。调用方据此 JIT 读取规格正文。
        """
        if not spec_ref:
            return None
        ref = spec_ref.strip()
        m = _CIP_RE.match(ref)
        if m:
            return {"layer": "cip", "repo": "cowboy",
                    "path_glob": f"docs/cips/cip-{int(m.group(1))}-*.md"}
        if ref.upper() in ("WP", "WHITEPAPER"):
            return {"layer": "whitepaper", "repo": "cowboy",
                    "path_glob": "docs/whitepaper/cowboy-technical-whitepaper.md"}
        return None

    def list_invariants(self, scope: dict) -> list[InvariantDef]:
        out = []
        if scope.get("repo") == "node":
            out.extend(_ECON_INVARIANTS)
            if any(p.startswith(_STATE_PREFIXES) for p in scope.get("diff_paths", [])):
                out.extend(_STATE_INVARIANTS)
            if any(p.startswith(_CRYPTO_PREFIXES) for p in scope.get("diff_paths", [])):
                out.extend(_CRYPTO_INVARIANTS)
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
        for hz in self.security_hazards(scope):
            reasons.append(f"security-hazard:{hz['id']} (review-only)")
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
                "security_hazards": self.security_hazards(scope),
                "review_dimensions": [d["name"] for d in self.review_plan(tier)]}

    def security_hazards(self, scope: dict) -> list[dict]:
        """否定性/对抗性安全危险点 (信任边)。命中即附一条 review lens 提示。

        与不变量分开:`invariant_able=False` 的危险点**不可**用功能往返 proptest 固化
        (机密性/IND-CPA 这类在脆弱构造上 roundtrip 照样为绿),只能由 security review
        裁定。skill 应把这些 prompt 注入对抗 review,且棘轮遇到这类根因不得 spawn
        roundtrip 不变量 (见 ratchet_guidance)。"""
        paths = scope.get("diff_paths", [])
        out = []
        for hz in SECURITY_HAZARDS:
            if any(p.startswith(hz.when_paths) for p in paths):
                out.append({"id": hz.id, "title": hz.title, "prompt": hz.prompt,
                            "invariant_able": hz.invariant_able})
        return out

    def ratchet_guidance(self, root_cause_class: str) -> dict:
        """棘轮形状守门:否定性属性 (机密性/保密/IND-CPA/泄露/越权) 不可往返化。

        遇到这些根因,spawn 一条功能 roundtrip proptest 会造出"绿着却漏"的假覆盖
        (正是 node #470 的教训:crypto.cbss_ibe_roundtrip 漏掉了 confidentiality break)。
        返回 invariant_able=False 时,permanent guard 应是一条 review-lens 危险点
        (SecurityHazard),而非 proptest。"""
        rc = (root_cause_class or "").lower()
        negative = ("confidential", "secrecy", "ind-cpa", "ind_cpa", "leak",
                    "exposure", "disclos", "auth", "privilege", "side-channel",
                    "side_channel")
        if any(k in rc for k in negative):
            return {"invariant_able": False, "preferred_shape": "review-hazard",
                    "reason": ("negative/adversarial property — a functional roundtrip "
                               "proptest is green on the vulnerable construction; spawn a "
                               "review-lens SecurityHazard, not a roundtrip invariant.")}
        return {"invariant_able": True, "preferred_shape": "proptest",
                "reason": "functional/safety property — a property test can express it."}

    def parse_spec_requirements(self, text: str) -> list[dict]:
        """⑤ parse_spec_requirements 种子: 从规格正文抽 RFC2119 规范性条款作为候选
        requirement (要求级 conformance 的分母侧)。启发式、逐行扫描:每行取其中最强的
        RFC2119 关键字 (大写才算规范性);MUST/SHALL/REQUIRED=must, SHOULD/RECOMMENDED=
        should, MAY/OPTIONAL=may。代码块/示例里的关键字可能误抽,属已知粗粒度局限。
        """
        reqs = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            for kw, level, rx in _RFC2119_RE:
                if rx.search(line):
                    reqs.append({"id": f"req-{len(reqs) + 1}", "level": level,
                                 "keyword": kw, "text": line})
                    break
        return reqs

    def conformance_matrix(self) -> dict:
        """Spec-ref → [invariant ids] coverage (⑤ ConformanceGov 矩阵种子 / ⑦
        conformance%). 只计 spec_ref 能解析到真实规格源的不变量;不可解析的标签
        (内部 tag) 不计入,以免虚报覆盖。调用方可拿全 CIP 集与之做差得未覆盖项。
        """
        out: dict = {}
        all_defs = (list(_ECON_INVARIANTS) + list(_CONTRACT_INVARIANTS.values())
                    + list(_STATE_INVARIANTS) + list(_CRYPTO_INVARIANTS))
        for inv in all_defs:
            if self.resolve_spec_ref(inv.spec_ref) is None:
                continue
            out.setdefault(inv.spec_ref, []).append(inv.id)
        return out

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
