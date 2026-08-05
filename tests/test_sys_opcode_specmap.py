"""Additional spawned check for escape esc-20260608-cip12-cip16-opcode-collision
(COW-2399 item 3). Sits alongside the escape's compile-time guard
`contract.sys_opcode_uniqueness` (node `#[deny(unreachable_patterns)]` + test) as
the marshal-side spec<->codec reconciliation; the escape registry's single
`spawned_check` slot stays pointed at the node guard.

The opcode analog of `system_actor_addrmap`. The address side reconciles the WP
§9.1 address table against `system_actors.rs`; this reconciles explicit
`opcode <N>` citations in the cowboy spec corpus against the deployed
`pub const SYS_<NAME>: u8 = <N>` constants in `cowboy-protocol-codec`.

run_command:
    /home/ubuntu/workspace/marshal/.venv/bin/python -m pytest \
        tests/test_sys_opcode_specmap.py -q

Status: the deployed-side collision gate is hard (the codec is the wire-format
source of truth and must never double-allocate an opcode — the recurring class
behind esc-20260608-cip12-cip16-opcode-collision). The spec->code citation
reconciliation is a skeleton (xfail): the spec corpus cites opcodes in prose
without always pairing the SYS_ name, so name-level reconciliation needs the same
hardening the addrmap parser went through before flipping to a hard gate.
"""

import pytest

from marshal_core.checks.sys_opcode_specmap import (
    deployed_opcodes_in_code,
    find_opcode_collisions,
    find_spec_opcodes_not_in_code,
    parse_deployed_opcodes,
    spec_cited_opcodes,
)

# Deployed codec const form: `pub const SYS_<NAME>: u8 = <N>;`.
_CODE = """\
pub const TX_CATEGORY_SYSTEM: u8 = 0;
pub const SYS_CREATE_ACCOUNT: u8 = 0;
pub const SYS_TRANSFER: u8 = 1;
pub const SYS_RAS: u8 = 101;
pub const SYS_GC_NONCES: u8 = 151;
"""


def test_parse_deployed_opcodes_const_form():
    assert parse_deployed_opcodes(_CODE) == {
        "SYS_CREATE_ACCOUNT": 0,
        "SYS_TRANSFER": 1,
        "SYS_RAS": 101,
        "SYS_GC_NONCES": 151,
    }


def test_parse_deployed_opcodes_ignores_non_sys_consts():
    # TX_CATEGORY_* / ACTOR_* / LIB_* are a different opcode namespace and must
    # not be folded into the SYS_ set (their values legitimately overlap SYS_).
    assert "TX_CATEGORY_SYSTEM" not in parse_deployed_opcodes(_CODE)


def test_find_opcode_collisions_flags_duplicate():
    dup = dict(parse_deployed_opcodes(_CODE), SYS_SHADOW=101)  # collides with SYS_RAS
    cols = find_opcode_collisions(dup)
    assert cols.get(101) == {"SYS_RAS", "SYS_SHADOW"}


def test_find_opcode_collisions_clean_when_unique():
    assert find_opcode_collisions(parse_deployed_opcodes(_CODE)) == {}


def test_spec_cited_opcodes_anchors_on_keyword():
    # Only the explicit `opcode N` keyword is a reliable signal — bare table
    # cells (`| 100 |` gas costs, exit codes, rate limits) must NOT be swept in.
    md = (
        "The handler uses opcode 101 for RAS.\n"
        "| 100 | 0.05 | gas-cost row, not an opcode |\n"
        "Renumber via `opcode 40` if needed.\n"
    )
    assert spec_cited_opcodes(md) == {101, 40}
    assert 100 not in spec_cited_opcodes(md)


def test_find_spec_opcodes_not_in_code():
    deployed = parse_deployed_opcodes(_CODE)  # {0,1,101,151}
    cited = {101, 200}  # 200 is not deployed
    assert find_spec_opcodes_not_in_code(cited, deployed) == {200}


# --- Live anchors (deterministic: read from a fixed git ref) -------------------


def test_deployed_opcodes_no_collision():
    """Hard gate: the deployed codec must never double-allocate a SYS_ opcode."""
    deployed = deployed_opcodes_in_code()
    cols = find_opcode_collisions(deployed)
    assert not cols, "deployed SYS_ opcode collisions: " + ", ".join(
        f"{op}: {sorted(names)}" for op, names in cols.items()
    )


def test_deployed_opcode_set_anchor():
    """Sanity anchor: the codec currently defines 136 SYS_ opcodes in 0..=151."""
    deployed = deployed_opcodes_in_code()
    assert len(deployed) == 136, f"expected 136 SYS_ opcodes, got {len(deployed)}"
    assert min(deployed.values()) == 0 and max(deployed.values()) == 151


@pytest.mark.xfail(
    reason="COW-2399 item 3 skeleton: spec corpus cites opcodes in prose without "
    "always pairing the SYS_ name; harden the spec parser (per-CIP framing, "
    "name proximity) before flipping this spec->code reconciliation to a hard gate.",
    strict=False,
)
def test_spec_cited_opcodes_all_deployed():
    deployed = deployed_opcodes_in_code()
    missing = find_spec_opcodes_not_in_code(spec_cited_opcodes(), deployed)
    assert not missing, "spec cites opcodes the codec does not deploy: " + ", ".join(
        f"{m}" for m in sorted(missing)
    )
