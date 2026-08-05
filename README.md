# Marshal

A general-purpose quality-engineering platform. Marshal gathers any project's
quality-engineering content into pluggable Domain Packs, then lands it in a
pre-merge gate through a deterministic CLI, a knowledge core, and executors.
Cowboy is the first Domain Pack bundled in this repository, but it is not the
boundary of the platform itself.

This README is the complete guide — how Marshal works, how to install and use it
locally, and how to deploy it (the brain service, the CI action, the reviewer
skill, and the plan-gate MCP server).

## Contents

- [How it works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Install](#install)
- [Command-line usage](#command-line-usage)
- [Concept registry & plan gate](#concept-registry--plan-gate)
- [Deployment](#deployment)
  - [1. The brain service](#1-the-brain-service)
  - [2. The GitHub Action (managed repos)](#2-the-github-action-managed-repos)
  - [3. The `/marshal` reviewer skill](#3-the-marshal-reviewer-skill)
  - [4. The plan-gate MCP server](#4-the-plan-gate-mcp-server)
- [Configuration](#configuration)
- [Repository layout](#repository-layout)
- [Development](#development)

## How it works

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

The platform is domain-agnostic; everything project-specific lives in a Domain
Pack. The bundled `marshal_pack_cowboy` provides the rules, invariants, spec
resolution, and concept registry for the Cowboy L1 codebase.

## Prerequisites

- **Python ≥ 3.11**
- **git** (diffs and refs drive classification)
- Optional: **`zizmor`** for `ci-scan` (GitHub Actions security audit) — installed
  via the `ci` extra
- Optional: the **`mcp`** SDK for the plan-gate MCP server — installed via the
  `mcp` extra

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,ci]"
pytest -q
```

The optional-dependency extras:

| Extra | Pulls in | Needed for |
|---|---|---|
| `dev` | `pytest`, `pytest-asyncio`, `ruff` | running the test suite and linting |
| `ci` | `zizmor` | the `ci-scan` command (GitHub Actions audit) |
| `mcp` | `mcp` (official SDK) | the plan-gate MCP server |

The core dependencies (`fastapi`, `uvicorn`, `pydantic`, `sqlalchemy`, `httpx`,
`pyyaml`) install with the base package and are what the brain service needs.

## Command-line usage

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

A self-contained, offline visual browser for the tree lives at
`docs/concept-tree.html` (a node-link diagram colored by importance tier, with
each concept's `depends_on` chain and transitive blast radius). The `docs/`
directory is kept local and is not published to the remote — see
[Repository layout](#repository-layout).

## Deployment

Marshal has four deployable pieces. A minimal setup for a team is: run **one
brain service**, wire the **GitHub Action** into each managed repo, and have
reviewers install the **`/marshal` skill** and register the **MCP server**
locally. Everything runs in shadow-safe mode — it records and reports, it never
blocks a merge directly.

### 1. The brain service

The brain is a FastAPI app (`marshal_core.adapters.api:app`). It exposes:

| Endpoint | Caller | Purpose |
|---|---|---|
| `POST /webhook` | GitHub PR webhook | Normalize a PR event and seed the job |
| `POST /plan` | the CI reporter | Return the invariants + run-commands for a change |
| `POST /results` | the CI reporter | Ingest structured results and return a shadow check-run |

Run it with uvicorn (the core install already includes both):

```bash
pip install -e .
MARSHAL_DB="sqlite:///$(pwd)/marshal.db" \
  uvicorn marshal_core.adapters.api:app --host 0.0.0.0 --port 8000
```

The knowledge-core tables are created automatically on startup. For a durable
store, point `MARSHAL_DB` at any SQLAlchemy URL (e.g.
`postgresql://user:pass@host/marshal`). Run it behind your usual reverse proxy /
process manager.

> **Run a single process.** The brain caches recent PR events in memory to
> correlate `/plan` with the later `/results`. Multiple workers would not share
> that cache, so keep it to one uvicorn worker (scale by fronting several
> single-worker instances only if each repo maps to one instance).

### 2. The GitHub Action (managed repos)

The repository ships a composite action that lets a managed repo pull its
applicable invariants from the brain in CI and report results back. Add it to
the repo's workflow:

```yaml
jobs:
  marshal:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: shawhanken/marshal@main
        with:
          brain-url: https://marshal.example.com   # your brain service
          repo: node                                # this repo's id in the Domain Pack
```

- `base-ref` is optional; when omitted the action compares `HEAD~1...HEAD`.
- The action computes the changed paths, then runs the bundled reporter, which
  `POST`s to `{brain-url}/plan` and `{brain-url}/results`. The reporter uses only
  the Python standard library, so the runner needs **Python 3 and network access
  to `brain-url`** — nothing to install.
- It is shadow-safe: the returned check-run is informational and never blocks.

### 3. The `/marshal` reviewer skill

For a reviewer running Claude Code (or a Codex/agents setup) locally:

```bash
python -m marshal_core.cli setup
```

`setup` links the in-repo `.claude/skills/marshal` to `~/.claude/skills/marshal`
(and mirrors `.agents/skills/marshal` for Codex/agents), then checks that the
Python imports and `zizmor` are available. After it runs, `/marshal` is available
as a slash command.

### 4. The plan-gate MCP server

The plan gate is also exposed as an MCP tool (`marshal_plan_review`) so any agent
can run it. It is a stdio server — no separate service to host; the MCP client
spawns it on demand. The client and server must be on the same host (the tool
reads `concepts_dir` / `repo_roots` from the local filesystem).

Install the extra, then register the server:

```bash
pip install -e ".[mcp]"
```

**Claude Code** (user scope — works from any directory):

```bash
claude mcp add -s user marshal-plan-gate \
  /path/to/marshal/.venv/bin/python -- -m marshal_core.mcp_server
```

Verify with `claude mcp list` (`marshal-plan-gate … ✔ Connected`).

> A project-root `.mcp.json` also works, but it only loads when Claude Code is
> launched from that directory and needs a first-run approval — `claude mcp add
> -s user` avoids both.

**Codex / Opencode** use a different config file (`~/.codex/config.toml`,
`opencode.json`'s `mcp` section), but the same command and args:

```
command: /path/to/marshal/.venv/bin/python
args:    ["-m", "marshal_core.mcp_server"]
```

The calling agent maps a plan to concept `touches`; the tool runs the
deterministic `plan-cost` computation and returns the same neutral cost picture
described above (verdict always `cost-only`).

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `MARSHAL_HOME` | The current source-repo root | Where `.claude/skills/marshal` and the default database are located |
| `MARSHAL_DB` | `sqlite:///$MARSHAL_HOME/marshal.db` | The SQLAlchemy database URL — used by both the CLI and the brain service |

`ci-scan` requires `zizmor`. Installing `.[ci]` is recommended; when it is
absent, the command returns non-zero and emits `degraded: true`, pushing the
gate above it into manual judgment so nothing passes falsely.

## Repository layout

| Path | Contents |
|---|---|
| `src/marshal_core/` | Domain-agnostic core: CLI, contracts, knowledge core, review aggregation, GitHub adapter, orchestrator, invariant gate, reporter |
| `src/marshal_core/adapters/api.py` | The brain: FastAPI app (`/webhook`, `/plan`, `/results`) |
| `src/marshal_core/executor/reporter.py` | The CI reporter the GitHub Action runs |
| `src/marshal_core/mcp_server.py` | The plan-gate MCP server exposing the `marshal_plan_review` tool |
| `src/marshal_pack_cowboy/` | The Cowboy Domain Pack: risk-classification rules, invariant catalog, spec resolution, CI-security checks |
| `src/marshal_pack_cowboy/concepts/` | The Cowboy concept registry: 32 concept pages (constitutional/high anchored to code) plus a spec-vs-code drift board |
| `.claude/skills/marshal/` | The Claude Code `/marshal` skill and its gate / review / conformance / ratchet flow docs |
| `.agents/skills/marshal/` | The Codex/agents-side copy of the same skill |
| `marshal.db` | The default SQLite knowledge core |
| `action.yml` | The GitHub composite action used by managed repos |
| `docs/` | Local-only working docs (methodology, architecture, plans, and the concept-tree browser) — **gitignored, not published to the remote** |
| `tests/` | Tests for the CLI, Domain Pack, knowledge core, gate, reporter, CI security, etc. |

## Development

```bash
pip install -e ".[dev,ci]"
pytest -q
ruff check src tests
```

The `docs/` directory holds fuller methodology, architecture, and
implementation notes; it is kept local (gitignored) rather than published.
