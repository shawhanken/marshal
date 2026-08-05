"""Spawned check for escape esc-20260605-wp-0x0d-addrmap.

run_command (registered in the invariant pack):
    /home/ubuntu/workspace/marshal/.venv/bin/python -m pytest \
        tests/test_system_actor_addrmap.py -q

Status — node #585 moved CIP-7 STREAM_KEY_MANAGER 0x12 -> 0x0D:
- The false `0x0D` code-deployed citations are purged (0x0D now exists in code),
  so `test_no_false_code_deployed_citations` is promoted to a hard, permanent
  gate (xfail dropped).
- The deployed code band is now 0x01..0x10 (node added 0x0E ROUTE_REGISTRY,
  0x0F GATEWAY_REGISTRY, 0x10 RECEIPT_REGISTRY); the contiguity anchor tracks that.
- The spec corpus still double-claims `0x0D` (STREAM_KEY_MANAGER vs ROUTE_REGISTRY
  across WP/CIP-7/CIP-28), so `test_system_actor_addrmap_consistent` stays `xfail`
  until CIP-28 / route-manifest reconciles ROUTE_REGISTRY off 0x0D.
"""

import pytest

from marshal_core.checks.system_actor_addrmap import (
    deployed_addresses_in_code,
    find_collisions,
    find_deployed_missing_from_wp_table,
    find_false_code_citations,
    parse_deployed_addresses,
    parse_spec_rows,
)


# --- Parser robustness (COW-2399 item 2) ---------------------------------------
# node#852 moved the canonical address constants from per-const
# `Address::from_low_u64(0xNN)` literals to a single
# `system_actor_addresses! { NAME = 0xNN, ... }` macro list. The original regex
# matched only the literal-call form, so against the macro source it parsed ZERO
# deployed addresses — silently blinding `find_deployed_missing_from_wp_table`
# (false negative) and reddening `find_false_code_citations` (false positive).
# `parse_deployed_addresses` must read both forms; these pin that.

_MACRO_FORM = """\
macro_rules! system_actor_addresses {
    ($( $(#[$meta:meta])* $name:ident = $low:literal ),+ $(,)?) => {
        impl SystemActorAddresses {
            $( $(#[$meta])* pub const $name: Address = Address::from_low_u64($low); )+
            pub const ALL: &'static [(&'static str, Address)] = &[
                $( (stringify!($name), Address::from_low_u64($low)), )+
            ];
        }
    };
}

system_actor_addresses! {
    /// Runner Registry: `0x0000…0001`
    RUNNER_REGISTRY = 0x01,
    JOB_DISPATCHER = 0x02,
    DUAL_BASEFEE = 0x06,
    RECEIPT_REGISTRY = 0x10,
    /// CIP-33 Trading Post.
    TRADING_POST = 0x1E,
}
"""

_LITERAL_FORM = """\
impl SystemActorAddresses {
    pub const RUNNER_REGISTRY: Address = Address::from_low_u64(0x01);
    pub const JOB_DISPATCHER: Address = Address::from_low_u64(0x02);
    pub const DUAL_BASEFEE: Address = Address::from_low_u64(0x06);
    pub const RECEIPT_REGISTRY: Address = Address::from_low_u64(0x10);
    pub const TRADING_POST: Address = Address::from_low_u64(0x1E);
}
"""


def test_parse_deployed_addresses_macro_list_form():
    assert parse_deployed_addresses(_MACRO_FORM) == {0x01, 0x02, 0x06, 0x10, 0x1E}


def test_parse_deployed_addresses_legacy_literal_form():
    assert parse_deployed_addresses(_LITERAL_FORM) == {0x01, 0x02, 0x06, 0x10, 0x1E}


def test_parse_deployed_addresses_ignores_macro_placeholder():
    # The `$low` placeholder inside the macro *definition* must not parse as 0x..
    # (and must not crash); 0x00 must never appear.
    assert 0 not in parse_deployed_addresses(_MACRO_FORM)


def test_parse_deployed_addresses_does_not_sweep_unrelated_hex():
    # An `IDENT = 0xNN` const outside the macro-invocation block is not a
    # system-actor address and must not be swept in.
    text = _MACRO_FORM + "\nconst SOME_LIMIT: u64 = 0xFF;\nlet mask = 0x20;\n"
    assert parse_deployed_addresses(text) == {0x01, 0x02, 0x06, 0x10, 0x1E}


@pytest.fixture(scope="module")
def rows():
    return parse_spec_rows()


@pytest.mark.xfail(
    reason="esc-20260605-wp-0x0d-addrmap: 0x0D STREAM_KEY_MANAGER vs ROUTE_REGISTRY "
    "collision unresolved across WP/CIP-7/CIP-2/CIP-28. Drop xfail once reconciled.",
    strict=True,
)
def test_system_actor_addrmap_consistent(rows):
    collisions = find_collisions(rows)
    # 0x0D must NOT be the offender once the corpus is reconciled.
    assert 0x0D not in collisions, f"0x0D maps to multiple actors: {collisions.get(0x0D)}"
    assert not collisions, f"system-actor address collisions: {collisions}"


def test_no_false_code_deployed_citations(rows):
    # Hard gate since node #585 (STREAM_KEY_MANAGER -> 0x0D): no spec row may
    # cite a code-deployed system actor at an address the code does not deploy.
    deployed = deployed_addresses_in_code()
    false_cites = find_false_code_citations(rows, deployed)
    assert not false_cites, "false 'code-deployed' citations:\n" + "\n".join(
        f"  {r.source}  0x{r.addr:02X} -> {r.name}" for r in false_cites
    )


def test_deployed_band_matches_devnet():
    """Sanity anchor on the deployed set, read from `origin/devnet` (the active
    branch, which leads main). Currently 0x01..0x10 (the dense band) plus the
    non-dense 0x1E TRADING_POST (CIP-33). Bump when the next system actor lands."""
    deployed = deployed_addresses_in_code()
    assert deployed == set(range(0x01, 0x11)) | {0x1E}, sorted(hex(a) for a in deployed)


@pytest.mark.xfail(
    reason="COW-2399 item 2: the WP §9.1 table (on origin/main) omits TRADING_POST "
    "0x1E even though node devnet defines TRADING_POST_SYSTEM_ACTOR = 0x1E (a "
    "code-deployed actor the table MUST track, WP §9.1 note 1). The 0x1E row is "
    "staged in cowboy#214 — this xfails until that lands on main, then xpasses; "
    "promote to a hard gate (drop xfail) at that point.",
    strict=False,
)
def test_no_code_deployed_actor_missing_from_wp_table():
    missing = find_deployed_missing_from_wp_table()
    assert not missing, (
        "code-deployed system-actor addresses absent from the WP §9.1 table: "
        + ", ".join(f"0x{a:02X}" for a in sorted(missing))
    )
