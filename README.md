# Marshal

A general-purpose quality-engineering platform. Marshal gathers any project's
quality-engineering content into pluggable Domain Packs, then lands it in a
pre-merge gate through a deterministic CLI, a knowledge core, and executors.
Cowboy is the first Domain Pack bundled in this repository, but it is not the
boundary of the platform itself.

Core loop:

- **Risk classification**: judge a change's risk from its repo, diff paths,
  labels, and the full workflow text.
- **Invariant gate**: select, from the Domain Pack, the checks this change must run.
- **Adversarial AI review**: aggregate multi-perspective findings and adjudicate
  them by quorum and skeptic votes.
- **Escape ratchet**: any bug that slips through must be registered as an escape,
  and closing it must produce a permanent check.
- **Knowledge core**: record invariants, escapes, gate runs, audits, and metrics
  in SQLite.
- **Concept registry & plan gate**: model a domain as a tree of weighted,
  code-anchored concepts, and turn a plan into a deterministic, un-gameable
  concept-cost picture before the work starts.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,ci]"
pytest -q
```

Install the Claude Code `/marshal` skill:

```bash
python -m marshal_core.cli setup
```

`setup` links the in-repo `.claude/skills/marshal` to
`~/.claude/skills/marshal` and checks that the Python imports and `zizmor` are
available.

## Common commands

Every command runs through a thin CLI, with JSON in and JSON out:

```bash
python -m marshal_core.cli <command> [options]
```

| Command | Purpose |
|---|---|
| `classify --repo node --paths ...` | Risk-classify a change, emitting `high` / `mid` / `low` and review dimensions |
| `ci-scan --paths .github/workflows/ci.yml` | Audit GitHub Actions workflows with `zizmor`; degrades to an `escalate` signal when the tool is missing |
| `invariants --repo node --paths ...` | List the invariants that apply to this change, with their runnable commands |
| `review-quorum --findings-json ...` | Aggregate multi-perspective review findings — low-confidence noise is dropped, high-severity conclusions are escalated |
| `review-verify --votes-json ...` | Adjudicate each finding with a skeptic vote |
| `spec-source --ref CIP-3` | Resolve a spec reference to its location in the domain source |
| `spec-requirements --ref CIP-3 --spec-root <repo>` | Extract RFC2119 requirements from the spec text |
| `conformance [--spec-root <repo>]` | Emit the spec-to-invariant coverage matrix; with a spec root, report CIP coverage and gaps |
| `ratchet-open --escape-id ... --desc ...` | Register a quality escape |
| `ratchet-close --escape-id ... --spawned-check ... --inv-json ...` | Close an escape and register its permanent check |
| `gate-record --change-ref ... --verdict pass` | Persist a gate result |
| `metrics` | Summarize the quality metrics in the knowledge core |
| `setup` | Install the local skill link and run basic health checks |
| `concept-tree --concepts-dir ...` | Render the Domain Pack's concept hierarchy (importance, code-anchored vs spec-only) as a tree |
| `concept-list --concepts-dir ...` | List concepts with their metadata (importance, parent, anchors, spec refs) |
| `plan-cost --concepts-dir ... --touches ...` | Compute a neutral, deterministic concept-budget for a plan — weighted cost, blast radius, highest tier touched; never recommends do/don't |
| `onboard-estimate --repo ...` | Dry-run cost gate before onboarding a repo's HEAD into a concept registry |
| `onboard-detect --repo ...` | Deterministically detect concept signals from a repo's structure |
| `onboard-report --concepts-dir ...` | Produce the onboarding snapshot (concept pages + tech-debt) for human acceptance |

Examples:

```bash
python -m marshal_core.cli classify \
  --repo node \
  --paths execution/src/execution/transaction.rs

python -m marshal_core.cli invariants \
  --repo wallet \
  --paths src/lib/cbor.js

python -m marshal_core.cli ratchet-open \
  --escape-id esc-001 \
  --desc "encoding roundtrip missed malformed CBOR" \
  --root-cause determinism-gap
```

## Concept registry & plan gate

A Domain Pack can carry a **concept registry**: a tree of concept pages
(markdown with frontmatter — `concept_id`, `parent`, `importance`, `status`,
`depends_on`, `anchors`, `spec_refs`) that models the domain's load-bearing
ideas. Constitutional and high-importance concepts are anchored to real code
symbols, so their cost cannot be gamed. The Cowboy pack ships 32 such concepts
under `src/marshal_pack_cowboy/concepts/`.

**Onboard** a repo's HEAD into a registry with the dry-run → detect → report
flow (`onboard-estimate` / `onboard-detect` / `onboard-report`), then have a
human accept the drafted pages.

**Plan gate** — before implementing a plan, map it to concept `touches` and get
a neutral cost picture:

```bash
python -m marshal_core.cli plan-cost \
  --domain-pack cowboy \
  --concepts-dir src/marshal_pack_cowboy/concepts \
  --repo-root node=/path/to/node \
  --touches '[{"concept_id":"gas","op":"redefine"},
              {"concept_id":"cell-rent","op":"add","importance":"high","est_scope":"large"}]'
```

It returns `grounded_cost` (redefine, computed from the real tree — cannot be
gamed), `hinted_cost` (add, from the caller's scope hints — cross-check them),
`blast_radius` (concepts transitively affected), `impacted_repos`, and
`highest_tier_touched`. The verdict is always `cost-only`; the gate never tells
you whether to do the work.

### MCP server

The same plan gate is exposed as an MCP tool (`marshal_plan_review`) so any
agent (Claude Code, Codex, Opencode) can run it. Install the extra and register
the stdio server:

```bash
pip install -e ".[mcp]"
claude mcp add -s user marshal-plan-gate \
  /path/to/marshal/.venv/bin/python -- -m marshal_core.mcp_server
```

See [`docs/mcp-plan-gate-integration.md`](docs/mcp-plan-gate-integration.md) for
client configuration and the verified stdio-protocol evidence.

### Visual browser

[`docs/concept-tree.html`](docs/concept-tree.html) is a self-contained,
offline-openable browser for the concept tree — a node-link diagram colored by
importance tier, with each concept's `depends_on` chain and transitive blast
radius. No build step, no external assets.

## GitHub Action

The repository ships a composite action that lets a managed repo pull the
applicable invariants from the Marshal brain in CI and report back its results.
The current design is a shadow-safe mode: it records and reports, it does not
block directly.

```yaml
jobs:
  marshal:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: shawhanken/marshal@main
        with:
          brain-url: https://marshal.example.com
          repo: node
```

`base-ref` is optional; when omitted, the action defaults to comparing
`HEAD~1...HEAD`.

## Repository layout

| Path | Contents |
|---|---|
| `src/marshal_core/` | Domain-agnostic core: CLI, contracts, knowledge core, review aggregation, GitHub adapter, orchestrator, invariant gate, reporter |
| `src/marshal_pack_cowboy/` | The Cowboy Domain Pack: risk-classification rules, invariant catalog, spec resolution, CI-security checks |
| `src/marshal_pack_cowboy/concepts/` | The Cowboy concept registry: 32 concept pages (constitutional/high anchored to code) plus a spec-vs-code drift board |
| `src/marshal_core/mcp_server.py` | The plan-gate MCP server exposing the `marshal_plan_review` tool |
| `.claude/skills/marshal/` | The Claude Code `/marshal` skill and its gate / review / conformance / ratchet flow docs |
| `.agents/skills/marshal/` | The Codex/agents-side copy of the same skill |
| `marshal.db` | The default SQLite knowledge core |
| `action.yml` | The GitHub composite action used by managed repos |
| `docs/` | Methodology, architecture blueprints, and implementation plans |
| `tests/` | Tests for the CLI, Domain Pack, knowledge core, gate, reporter, CI security, etc. |

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `MARSHAL_HOME` | The current source-repo root | Where `.claude/skills/marshal` and the default database are located |
| `MARSHAL_DB` | `sqlite:///$MARSHAL_HOME/marshal.db` | The SQLAlchemy database URL |

`ci-scan` requires `zizmor`. Installing `.[ci]` is recommended; when it is
absent, the command returns non-zero and emits `degraded: true`, pushing the
gate above it into manual judgment so nothing passes falsely.

## Development

```bash
pip install -e ".[dev,ci]"
pytest -q
ruff check src tests
```

The README is the entry point; the fuller methodology, architecture, and
implementation plans live in [`docs/`](docs/README.md).
