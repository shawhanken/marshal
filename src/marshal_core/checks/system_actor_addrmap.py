"""System-actor address-map consistency check.

Permanent guard spawned by escape `esc-20260605-wp-0x0d-addrmap` (whitepaper PR
cowboyinc/cowboy#150 assigned `0x0D = Stream Key Manager`, contradicting CIP-7 /
CIP-2 / CIP-28 which place STREAM_KEY_MANAGER at `0x12` and `0x0D = ROUTE_REGISTRY`,
and citing `system_actors.rs:40` which does not define `0x0D` — code ends at `0x0C`).

The check parses every system-actor `address -> name` table across the cowboy spec
corpus and the node source, then asserts:

  (1) **No collision** — within a single canonical-allocation context, an address
      maps to exactly one actor name across the whole corpus.
  (2) **No false code citation** — any row tagged "code-deployed" or citing
      `system_actors.rs:<line>` resolves to a real `Address::from_low_u64(0x..)`
      constant actually present in `node/runner/src/system_actors.rs`.

NOTE (skeleton): the row parser below is intentionally minimal — it recognizes the
common `| 0xNN | Name ... |` markdown row shape. Implementers should harden the
parser (handle per-CIP "spec-only vs code-deployed" framing, revision tags like
"r2", and the `0x1D` virtual/intercepted actors which are legitimately not in
`system_actors.rs`) before flipping this from xfail to a hard gate. The known
`0x0D` collision MUST fail until the corpus is reconciled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

WORKSPACE = Path("/home/ubuntu/workspace")
COWBOY_DOCS = WORKSPACE / "cowboy" / "docs"
SYSTEM_ACTORS_RS = WORKSPACE / "node" / "runner" / "src" / "system_actors.rs"

# Addresses that are virtual / intercepted in pvm_host and legitimately NOT a
# deployed constant in system_actors.rs (do not flag as false code citation).
VIRTUAL_ADDRS = {0x1D}

_ROW = re.compile(r"\|\s*`?(0x[0-9A-Fa-f]{1,2})`?\s*\|\s*([^|]+?)\s*\|")
_RS_CONST = re.compile(r"Address::from_low_u64\((0x[0-9A-Fa-f]+)\)")
_CODE_DEPLOYED = re.compile(r"code-deployed|system_actors\.rs:\d+", re.IGNORECASE)


@dataclass(frozen=True)
class Row:
    addr: int
    name: str
    source: str  # "file:line"
    claims_code_deployed: bool


def deployed_addresses_in_code() -> set[int]:
    """Real `0x..` constants defined in node/runner/src/system_actors.rs."""
    if not SYSTEM_ACTORS_RS.exists():
        raise FileNotFoundError(SYSTEM_ACTORS_RS)
    text = SYSTEM_ACTORS_RS.read_text()
    return {int(m.group(1), 16) for m in _RS_CONST.finditer(text)}


def parse_spec_rows() -> list[Row]:
    rows: list[Row] = []
    for md in sorted(COWBOY_DOCS.rglob("*.md")):
        for i, line in enumerate(md.read_text(errors="replace").splitlines(), 1):
            m = _ROW.match(line.strip())
            if not m:
                continue
            try:
                addr = int(m.group(1), 16)
            except ValueError:
                continue
            # Only the single-byte system-actor band; skip storage-key tables etc.
            if addr > 0x1F:
                continue
            name = m.group(2).strip()
            rows.append(
                Row(
                    addr=addr,
                    name=name,
                    source=f"{md.relative_to(WORKSPACE)}:{i}",
                    claims_code_deployed=bool(_CODE_DEPLOYED.search(line)),
                )
            )
    return rows


def find_collisions(rows: list[Row]) -> dict[int, set[str]]:
    """address -> set of distinct actor-name stems mapped to it (collision if >1)."""
    # TODO(impl): canonicalize names (strip "(CIP-N ...)", revision tags) so that
    # "Stream Key Manager" and "STREAM_KEY_MANAGER" count as one. Until then,
    # callers should compare on a normalized stem.
    by_addr: dict[int, set[str]] = {}
    for r in rows:
        stem = re.split(r"[(\[]", r.name)[0].strip().lower().replace("_", " ")
        by_addr.setdefault(r.addr, set()).add(stem)
    return {a: names for a, names in by_addr.items() if len(names) > 1}


def find_false_code_citations(rows: list[Row], deployed: set[int]) -> list[Row]:
    return [
        r
        for r in rows
        if r.claims_code_deployed
        and r.addr not in deployed
        and r.addr not in VIRTUAL_ADDRS
    ]
