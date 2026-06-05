"""Spawned check for escape esc-20260605-wp-0x0d-addrmap.

run_command (registered in the invariant pack):
    /home/ubuntu/workspace/marshal/.venv/bin/python -m pytest \
        marshal_core/checks/test_system_actor_addrmap.py -q

This test currently encodes the FAILING corpus state as the regression target.
Once the cowboy spec corpus + node code agree on the `0x0D` owner and the false
`system_actors.rs:40` citations are purged, remove the `xfail` markers and the
two consistency asserts become a hard, permanent gate.
"""

import pytest

from marshal_core.checks.system_actor_addrmap import (
    deployed_addresses_in_code,
    find_collisions,
    find_false_code_citations,
    parse_spec_rows,
)


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


@pytest.mark.xfail(
    reason="esc-20260605-wp-0x0d-addrmap: CIP-10/14/15/16/18 cite system_actors.rs:40 "
    "for a 0x0D actor that does not exist (code ends at 0x0C).",
    strict=True,
)
def test_no_false_code_deployed_citations(rows):
    deployed = deployed_addresses_in_code()
    false_cites = find_false_code_citations(rows, deployed)
    assert not false_cites, "false 'code-deployed' citations:\n" + "\n".join(
        f"  {r.source}  0x{r.addr:02X} -> {r.name}" for r in false_cites
    )


def test_code_band_is_contiguous_0x01_to_0x0c():
    """Sanity anchor (passes today): code defines exactly 0x01..0x0C, no 0x0D."""
    deployed = deployed_addresses_in_code()
    assert deployed == set(range(0x01, 0x0D)), sorted(hex(a) for a in deployed)
    assert 0x0D not in deployed
