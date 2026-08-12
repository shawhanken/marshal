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
  - [3. Reviewer skills (Claude Code + Codex CLI)](#3-reviewer-skills-claude-code--codex-cli)
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
| `review-verify --votes-json ...` | Adjudicate each finding with a skeptic vote; with `--run-id` (+ optional `--findings-json`), also record each finding's adjudication chain into the review trace |
| `review-run-open --change-ref ... --host ... --model ...` | Open a review-trace run: record who (host/model/skill git rev) reviewed what; returns the `run_id` to pass to `review-verify` |
| `review-run-close --run-id ... --status complete\|degraded --evidence-json ...` | Close a trace with a reproducible evidence manifest; `complete` is rejected while a lens, command, or external scan is unresolved |
| `review-run-show --run-id ...` | Read back the run provenance, evidence manifest, and finding-level adjudication chain |
| `finding-verdict --finding-id ... --verdict accepted\|rejected\|modified` | Record the human's final verdict on a traced finding — the ground-truth label |
| `spec-source --ref CIP-3` | Resolve a spec reference to its location in the domain source |
| `spec-requirements --ref CIP-3 --spec-root <repo>` | Extract RFC2119 requirements from the spec text |
| `conformance [--spec-root <repo>]` | Emit the spec-to-invariant coverage matrix; with a spec root, report CIP coverage and gaps |
| `ratchet-open --escape-id ... --desc ...` | Register a quality escape |
| `ratchet-close --escape-id ... --spawned-check ... --inv-json ...` | Close an escape and register its permanent check |
| `gate-record --change-ref ... --verdict pass` | Persist a gate result |
| `metrics` | Summarize the quality metrics in the knowledge core |
| `worktree-diff [--repo-root ...] [--base ...]` | Collect one local diff including committed, staged, unstaged, and untracked content; auto-detect the remote default branch or require `--base` rather than silently omit commits |
| `setup [--host claude|codex]` | Install all reviewer skills for Claude Code and/or Codex, then run health checks |
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
reviewers install the **reviewer skills for their agent host** and register the **MCP server**
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
`postgresql://user:pass@host/marshal`). Additive trace-schema migrations are
applied automatically. Run it behind your usual reverse proxy / process manager.

### Review evidence manifests

Review traces are opened before the multi-lens review and closed afterwards. The
close command records references rather than copying logs, so another reviewer
can reproduce the exact scope and distinguish a completed check from a missing
one:

```bash
run=$(python -m marshal_core.cli review-run-open \
  --change-ref <head-sha> --repo node --mode deep --host codex --model <model> \
  --expected-lenses-json '["correctness"]' \
  --expected-commands-json '["invariants"]' \
  --expected-external-scans-json '["almanax"]' \
  | jq -r .run_id)
python -m marshal_core.cli review-run-close --run-id "$run" --status degraded \
  --evidence-json '{
    "head_sha": "<head-sha>", "base_sha": "<base-sha>", "tree_sha": "<tree-sha>",
    "platform": "linux-x86_64", "worktree": "/tmp/review-worktree",
    "toolchain": "python3.12", "context_ref": "node@<head-sha>",
    "steps": {
      "closure": {"status": "complete"},
      "scout": {"status": "degraded", "reason": "agent stalled"},
      "prove": {"status": "complete"},
      "invariant": {"status": "complete"}
    },
    "lenses": {
      "expected": ["correctness"], "returned": [], "missing": ["correctness"]
    },
    "commands": [
      {"name": "invariants", "status": "not_run", "reason": "agent stalled"}
    ],
    "external_scans": [
      {"name": "almanax", "status": "unavailable", "reason": "quota reached"}
    ]
  }'
```

The manifest should include commit/tree identifiers, closure/scout/prove and
invariant statuses, expected/returned/missing lenses, command/test counts with
log references, and external-scan status. An external scan with
`status=complete` must provide an integer `findings` count. For
`unavailable`/`degraded` scans, omit `findings` (or use null): unavailable is
not the same as zero findings. Use `review-run-show` to compare two reports.
A closed run is terminal: its head SHA must match the run change_ref, and its evidence and findings cannot be overwritten afterwards. Complete manifests must contain exactly closure/scout/prove/invariant stages, a partitioned expected/returned/missing lens set, executable command metadata with a zero exit status, and at least one external-scan record.

Planned event context is persisted in the knowledge core, while the in-memory
cache is only a fast path. `/plan` and `/results` can therefore use different
workers or survive a process restart when they share the same database. Use a
transactional shared database for multi-worker deployments; SQLite remains
best suited to a single brain process.

> ⚠️ **Do not expose the brain to the public internet.** The service currently
> has **no authentication**: `/webhook` does not verify GitHub's
> `X-Hub-Signature-256` webhook signature, and `/plan` / `/results` accept any
> caller. Because the CI reporter executes the commands the brain returns,
> anyone who can reach (or impersonate) the brain can run arbitrary commands on
> your CI runners. Deploy it only on a trusted internal network, reachable
> exclusively by your GitHub webhook forwarder and your own CI, until
> authentication lands.

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
  `POST`s to `{brain-url}/plan` and `{brain-url}/results`. Plans must declare a
  supported `executor_kind`; unknown or missing kinds are reported as `not_run`
  instead of passing on exit code alone. Each command has a 600-second limit and
  each reporter run has a 3600-second total limit. The reporter uses only
  the Python standard library, so the runner needs **Python 3 and network access
  to `brain-url`** — nothing to install.
- It is shadow-safe: the returned check-run is informational and never blocks.

### 3. Reviewer skills (Claude Code + Codex CLI)

Marshal ships three shared reviewer workflows plus a Codex-native PR sweep:

| Workflow | Claude Code | Codex CLI |
|---|---|---|
| Merge gate / ratchet | `/marshal` | `$marshal` |
| Repository onboarding | `/onboard` | `$onboard` |
| Neutral plan cost | `/plan-cost` | `$plan-cost` |
| Cross-repo PR deep-review sweep | Existing user-managed skill remains unchanged | `$marshal-pr-sweep` |

Install every bundled workflow for each host (three Claude workflows and four
Codex workflows):

```bash
python -m marshal_core.cli setup
```

Run `setup` from a Marshal source checkout installed with the editable-install
command in [Install](#install). The repository intentionally does not publish a
wheel/plugin containing these top-level skill assets.

`setup` links the Claude-specific sources under `.claude/skills/` into
`~/.claude/skills/` and the Codex-specific sources under `.agents/skills/` into
`~/.agents/skills/`. Concurrent setup processes are serialized. Each skill
destination is handled independently: a Codex conflict does not block safe Claude
links (or vice versa), and the command returns non-zero with per-destination
`conflicts` while preserving every safe installation. It also checks that the
Python imports and `zizmor` are available.

Existing symlinks to this checkout are reused. Live links to the same bundled skill
in an older recognizable Marshal checkout are migrated atomically. Real
directories/files, broken links, and links owned by another installation are
reported as conflicts and are never overwritten.

To install only one host:

```bash
python -m marshal_core.cli setup --host claude
python -m marshal_core.cli setup --host codex
```

Claude Code continues to use slash-form skill commands. In Codex, type `$` to
mention a skill explicitly, or use `/skills` to browse the installed skills.
When Codex runs a multi-lens review, it uses Codex subagents when that capability
is available; the Claude workflow remains unchanged.

The Codex-only `$marshal-pr-sweep` discovers open Cowboy PRs whose current head
has not received a Marshal deep review, applies CI/draft/CIP-10 filters, and runs
a bounded sequential batch. Its SHA markers are scoped to the authenticated
GitHub user, so an existing Claude sweep using the same account shares progress
without sharing files or logs. The existing `~/.claude/skills/marshal-pr-sweep`
installation is never read, replaced, or migrated by `setup`.

For an interactive run, invoke `$marshal-pr-sweep`. For cron, use the installed
Codex launcher:

```bash
MAX_PER_RUN=auto ~/.agents/skills/marshal-pr-sweep/scripts/run_sweep.sh
```

The launcher uses `codex exec`, `workspace-write`, explicit network access, and
`approval_policy=never`; it never enables `--yolo`. Its default writable workspace
is the dedicated `~/.local/state/marshal-pr-sweep/workspace/`, not the parent that
may contain sibling repositories. PR content is treated as untrusted data, and
Marshal itself remains read-only reference material. Logs go to
`~/.local/state/marshal-pr-sweep/`. If invariant checkouts require writes blocked
by protected Git metadata, the run degrades safely. Only on an isolated trusted
runner, opt in explicitly with `SANDBOX_MODE=danger-full-access`. Codex App
scheduled tasks can instead use: `Use $marshal-pr-sweep to run one scheduled
sweep cycle.`

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

**Codex CLI** (user scope, works from any directory):

```bash
codex mcp add marshal-plan-gate -- \
  /path/to/marshal/.venv/bin/python -m marshal_core.mcp_server
```

Verify with `codex mcp list`; inside the Codex TUI, use `/mcp`. The equivalent
manual entry in `~/.codex/config.toml` is:

```toml
[mcp_servers.marshal-plan-gate]
command = "/path/to/marshal/.venv/bin/python"
args = ["-m", "marshal_core.mcp_server"]
```

Opencode can use the same command and args in its `opencode.json` MCP section.

The calling agent maps a plan to concept `touches`; the tool runs the
deterministic `plan-cost` computation and returns the same neutral cost picture
described above (verdict always `cost-only`).

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `MARSHAL_HOME` | The current source-repo root | Where `.claude/skills`, `.agents/skills`, and the default database are located |
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
| `.claude/skills/` | Claude Code workflows: `/marshal`, `/onboard`, and `/plan-cost` |
| `.agents/skills/` | Codex-native workflows: `$marshal`, `$onboard`, and `$plan-cost` |
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

## License

Marshal is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE).
Noncommercial use — personal, research, educational, and nonprofit — is free.

**Any commercial use requires a separate commercial license.** To obtain
commercial authorization, contact <shawhanken@gmail.com>.
